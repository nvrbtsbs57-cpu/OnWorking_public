from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Any, List

import requests

from bot.config import IndexerConfig, ChainRpcConfig
from .storage import IndexerStorage

logger = logging.getLogger(__name__)


# =====================================================================
#  RPC EVM simple (JSON-RPC)
# =====================================================================

class EvmRpcError(Exception):
    pass


class EvmRpcClient:
    def __init__(self, rpc_url: str) -> None:
        self.rpc_url = rpc_url

    def _call(self, method: str, params: List[Any]) -> Any:
        try:
            resp = requests.post(
                self.rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params,
                },
                timeout=10,
            )
        except Exception as e:
            raise EvmRpcError(f"RPC request error: {e}") from e

        if resp.status_code != 200:
            raise EvmRpcError(f"RPC HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        if "error" in data and data["error"] is not None:
            raise EvmRpcError(f"RPC error: {data['error']}")

        return data.get("result")

    def get_block_number(self) -> int:
        result = self._call("eth_blockNumber", [])
        return int(result, 16)

    def get_block_by_number(self, number: int, full_transactions: bool = False) -> dict[str, Any]:
        hex_num = hex(number)
        result = self._call("eth_getBlockByNumber", [hex_num, full_transactions])
        if result is None:
            raise EvmRpcError(f"Block {number} not found")
        return result


# =====================================================================
#  State
# =====================================================================

@dataclass
class ChainState:
    last_synced_block: int | None = None
    rpc_client: EvmRpcClient | None = None


@dataclass
class IndexerState:
    chains: Dict[str, ChainState] = field(default_factory=dict)


# =====================================================================
#  IndexerEngine V2 – on-chain
# =====================================================================

class IndexerEngine:
    """
    Indexer V2 GODMODE :
    - support multi-chaînes EVM via JSON-RPC
    - suit la tête des blocs
    - stocke un résumé par bloc (hash, number, timestamp, tx_count)
    """

    def __init__(self, cfg: IndexerConfig) -> None:
        self.cfg = cfg
        self.storage = IndexerStorage(cfg.storage_path)
        self.state = IndexerState()
        self._stop_event = threading.Event()

        self._init_chains()

    # -------------------------------------------------------------------
    # Initialisation
    # -------------------------------------------------------------------

    def _init_chains(self) -> None:
        if not self.cfg.chains:
            logger.warning("IndexerEngine initialized with NO chains configured")

        for name, chain_cfg in self.cfg.chains.items():
            if not chain_cfg.enabled:
                logger.info("Chain %s is disabled in config, skipping", name)
                continue

            client = EvmRpcClient(chain_cfg.rpc_url)
            self.state.chains[name] = ChainState(
                last_synced_block=None,
                rpc_client=client,
            )

            logger.info("Chain %s configured for indexing — rpc=%s", name, chain_cfg.rpc_url)

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def start_in_thread(self, name: str = "indexer") -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name=name, daemon=True)
        t.start()
        return t

    def run_forever(self) -> None:
        logger.info(
            "IndexerEngine V2 started (poll_interval=%.3fs, chains=%s)",
            self.cfg.poll_interval_seconds,
            list(self.state.chains.keys()),
        )

        try:
            while not self._stop_event.is_set():
                self._poll_all_chains()
                time.sleep(self.cfg.poll_interval_seconds)
        except Exception:
            logger.exception("Fatal error in IndexerEngine")
            raise

    def stop(self) -> None:
        self._stop_event.set()

    # -------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------

    def _poll_all_chains(self) -> None:
        if not self.state.chains:
            logger.debug("IndexerEngine has no active chains to poll")
            return

        for name, chain_state in self.state.chains.items():
            chain_cfg = self.cfg.chains.get(name)
            if chain_cfg is None:
                continue
            if not chain_cfg.enabled:
                continue

            try:
                self._poll_chain(name, chain_cfg, chain_state)
            except EvmRpcError:
                logger.exception("RPC error while polling chain %s", name)
            except Exception:
                logger.exception("Unexpected error while polling chain %s", name)

    def _poll_chain(self, name: str, cfg: ChainRpcConfig, state: ChainState) -> None:
        if state.rpc_client is None:
            logger.error("No RPC client for chain %s", name)
            return

        latest_block = state.rpc_client.get_block_number()
        logger.debug("Chain %s — latest_block=%d", name, latest_block)

        if state.last_synced_block is None:
            if cfg.start_block > 0:
                state.last_synced_block = cfg.start_block - 1
            else:
                state.last_synced_block = max(latest_block - 1, 0)

            logger.info(
                "Chain %s initial sync position set to %d (latest=%d, start_block=%d)",
                name,
                state.last_synced_block,
                latest_block,
                cfg.start_block,
            )

        from_block = state.last_synced_block + 1
        if from_block > latest_block:
            logger.debug(
                "Chain %s is up-to-date (last_synced=%d, latest=%d)",
                name,
                state.last_synced_block,
                latest_block,
            )
            return

        to_block = min(from_block + cfg.max_blocks_per_poll - 1, latest_block)

        logger.info(
            "Chain %s — syncing blocks [%d, %d] (latest=%d)",
            name,
            from_block,
            to_block,
            latest_block,
        )

        for block_number in range(from_block, to_block + 1):
            self._process_block(name, state, block_number)

        state.last_synced_block = to_block

        self.storage.write_meta(
            name,
            {
                "last_synced_block": state.last_synced_block,
                "latest_seen_block": latest_block,
                "ts": time.time(),
            },
        )

    def _process_block(self, chain_name: str, state: ChainState, block_number: int) -> None:
        if state.rpc_client is None:
            return

        block = state.rpc_client.get_block_by_number(block_number, full_transactions=False)

        ts_hex = block.get("timestamp", "0x0")
        try:
            ts_int = int(ts_hex, 16)
        except Exception:
            ts_int = 0

        txs = block.get("transactions", [])
        tx_count = len(txs) if isinstance(txs, list) else 0

        number_hex = block.get("number")
        if number_hex:
            try:
                number_int = int(number_hex, 16)
            except Exception:
                number_int = block_number
        else:
            number_int = block_number

        summary = {
            "chain": chain_name,
            "number": number_int,
            "hash": block.get("hash"),
            "parent_hash": block.get("parentHash"),
            "timestamp": ts_int,
            "transaction_count": tx_count,
        }

        self.storage.write_block(chain_name, summary)

        logger.info(
            "Indexed block %d on %s — hash=%s txs=%d",
            summary["number"],
            chain_name,
            summary["hash"],
            tx_count,
        )
