import asyncio
from typing import Any, Dict, List, Optional

import aiohttp

from .base_chain import BaseChain, ChainConfig
from bot.core.logging import get_logger

logger = get_logger(__name__)


class EvmChain(BaseChain):
    """
    EVM chain avec vraie connexion RPC + logs ERC20.

    - eth_blockNumber pour récupérer le dernier bloc réel
    - eth_getLogs si des log_addresses sont configurées
    - fallback automatique en mode mock si le RPC ne répond pas
    """

    def __init__(self, config: ChainConfig) -> None:
        super().__init__(config)
        self._session: Optional[aiohttp.ClientSession] = None
        self._mock_counter: int = 0
        self._use_mock: bool = not config.rpc_url or config.rpc_url.lower() in {"mock", "none"}
        self._log_addresses: List[str] = [a.lower() for a in (config.log_addresses or [])]

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _rpc_call(self, method: str, params: List[Any]) -> Any:
        if self._use_mock:
            raise RuntimeError("RPC disabled (mock mode)")

        session = await self._get_session()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        async with session.post(self.config.rpc_url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data["result"]

    async def fetch_latest_block_number(self) -> int:
        # Mode mock -> simple compteur qui avance
        if self._use_mock:
            await asyncio.sleep(0.01)
            self._mock_counter += 1
            return self._mock_counter

        try:
            raw = await self._rpc_call("eth_blockNumber", [])
            block_num = int(raw, 16)
            return block_num
        except Exception as e:
            logger.error(
                f"[{self.name}] eth_blockNumber failed on {self.config.rpc_url}, "
                f"switching to mock mode: {e}"
            )
            self._use_mock = True
            return await self.fetch_latest_block_number()

    # ========== LOGS DECODING ==========

    @staticmethod
    def _classify_event(topics: List[str]) -> str:
        """
        Classifie quelques events courants EVM.
        On matche uniquement sur les prefix des topics (plus robuste si on ne met pas tout le hash).
        """
        if not topics:
            return "evm_unknown"

        t0 = topics[0].lower()

        # ERC20 Transfer(address,address,uint256)
        if t0.startswith("0xddf252ad"):
            return "erc20_transfer"

        # ERC20 Approval(address,address,uint256)
        if t0.startswith("0x8c5be1e5"):
            return "erc20_approval"

        return "evm_unknown"

    @staticmethod
    def _safe_int(hex_str: Optional[str]) -> int:
        if not isinstance(hex_str, str):
            return 0
        try:
            return int(hex_str, 16)
        except Exception:
            return 0

    def _normalize_log(self, log: Dict[str, Any]) -> Dict[str, Any]:
        topics_raw = log.get("topics") or []
        topics: List[str] = [str(t).lower() for t in topics_raw]

        ev_type = self._classify_event(topics)

        block = self._safe_int(log.get("blockNumber"))
        tx_hash = log.get("transactionHash")
        contract = str(log.get("address", "")).lower()

        base: Dict[str, Any] = {
            "chain": self.name,
            "block": block,
            "type": ev_type,
            "tx_hash": tx_hash,
            "contract": contract,
            "raw_log": log,
            "chain_id": self.config.chain_id,
            "source": "rpc",
        }

        # Décodage minimal pour Transfer / Approval
        data_hex = log.get("data", "0x0")
        value = self._safe_int(data_hex)

        if ev_type == "erc20_transfer":
            from_addr = topics[1][-40:] if len(topics) > 1 else ""
            to_addr = topics[2][-40:] if len(topics) > 2 else ""
            base["event"] = {
                "name": "Transfer",
                "from": f"0x{from_addr}" if from_addr else None,
                "to": f"0x{to_addr}" if to_addr else None,
                "value_raw": value,
            }

        elif ev_type == "erc20_approval":
            owner = topics[1][-40:] if len(topics) > 1 else ""
            spender = topics[2][-40:] if len(topics) > 2 else ""
            base["event"] = {
                "name": "Approval",
                "owner": f"0x{owner}" if owner else None,
                "spender": f"0x{spender}" if spender else None,
                "value_raw": value,
            }

        else:
            # Event générique non décodé
            base["event"] = {
                "name": "Unknown",
                "topics": topics,
                "data": data_hex,
            }

        return base

    async def _fetch_logs(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        """
        Récupère les logs via eth_getLogs pour les adresses suivies.
        Si aucune adresse configurée -> renvoie une liste vide.
        """
        if not self._log_addresses:
            return []

        # Filtre de base
        log_filter: Dict[str, Any] = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": self._log_addresses,
        }

        try:
            result = await self._rpc_call("eth_getLogs", [log_filter])
            if not isinstance(result, list):
                return []
            return [self._normalize_log(log) for log in result]
        except Exception as e:
            logger.error(f"[{self.name}] eth_getLogs failed: {e}")
            return []

    async def fetch_new_events(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        """
        - Si RPC mock ou pas de log_addresses configurées -> fake events comme avant.
        - Sinon -> vrais logs ERC20 sur les adresses suivies.
        """
        # Mode mock ou pas d'adresses suivies -> comportement précédent
        if self._use_mock or not self._log_addresses:
            await asyncio.sleep(0.01)
            events: List[Dict[str, Any]] = []
            for block in range(from_block, to_block + 1):
                events.append(
                    {
                        "chain": self.name,
                        "block": block,
                        "type": "fake_swap",
                        "data": {
                            "tx_hash": f"0x{block:064x}",
                            "amount_in": 1.0,
                            "amount_out": 0.99,
                            "source": "mock",
                            "chain_id": self.config.chain_id,
                        },
                    }
                )
            return events

        # Mode RPC + adresses configurées -> vrais logs
        return await self._fetch_logs(from_block, to_block)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
