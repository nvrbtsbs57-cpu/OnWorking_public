from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from web3 import Web3

logger = logging.getLogger(__name__)


@dataclass
class WhaleFeedConfig:
    rpc_url: str
    token_address: str         # ERC20 (USDC)
    token_symbol: str = "USDC"
    token_decimals: int = 6
    min_notional_usd: float = 50_000.0
    chain: str = "ethereum"


@dataclass
class WhaleTxFeed:
    """
    Détecte des transferts ERC20 "whales" (taille min_notional_usd).

    - Émet des events type "whale_tx"
    - Compatibles avec AgentEngine (on ne passe PAS par OrderFlow)
    """

    cfg: WhaleFeedConfig
    _w3: Web3 = field(init=False)
    _token: str = field(init=False)
    _transfer_topic: str = field(init=False)
    _last_block: Optional[int] = field(default=None, init=False)

    def __post_init__(self):
        self._w3 = Web3(Web3.HTTPProvider(self.cfg.rpc_url))
        self._token = Web3.to_checksum_address(self.cfg.token_address)
        self._transfer_topic = Web3.keccak(
            text="Transfer(address,address,uint256)"
        ).hex()

        try:
            current_block = self._w3.eth.block_number
        except Exception as e:
            logger.error("WhaleTxFeed: cannot get latest block: %s", e)
            current_block = None

        self._last_block = current_block
        logger.info(
            "WhaleTxFeed initialized (token=%s, start_block=%s, min_notional=%s USD)",
            self.cfg.token_symbol,
            self._last_block,
            self.cfg.min_notional_usd,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def poll(self) -> List[Dict[str, Any]]:
        """
        Récupère les nouveaux transferts "whale".
        """
        events: List[Dict[str, Any]] = []

        try:
            latest = self._w3.eth.block_number
        except Exception as e:
            logger.error("WhaleTxFeed: error getting block_number: %s", e)
            return events

        if self._last_block is None:
            self._last_block = latest
            return events

        if latest <= self._last_block:
            return events

        from_block = self._last_block + 1
        to_block = latest

        try:
            logs = self._w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": to_block,
                    "address": self._token,
                    "topics": [self._transfer_topic],
                }
            )
        except Exception as e:
            logger.error(
                "WhaleTxFeed: get_logs error (blocks %s-%s): %s",
                from_block,
                to_block,
                e,
            )
            self._last_block = latest
            return events

        for log in logs:
            try:
                ev = self._parse_transfer_log(log)
                if ev is not None:
                    events.append(ev)
            except Exception as e:
                logger.debug("WhaleTxFeed: failed to parse log: %s", e)

        self._last_block = latest
        if events:
            logger.info(
                "WhaleTxFeed: %s whale transfers detected from blocks %s-%s",
                len(events),
                from_block,
                to_block,
            )
        return events

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _parse_transfer_log(self, log: Any) -> Optional[Dict[str, Any]]:
        topics = log["topics"]
        if len(topics) < 3:
            return None

        from_addr = "0x" + topics[1].hex()[-40:]
        to_addr = "0x" + topics[2].hex()[-40:]

        value = int(log["data"], 16)
        amount = value / (10 ** self.cfg.token_decimals)
        notional_usd = amount  # 1 USDC ~ 1 USD

        if notional_usd < self.cfg.min_notional_usd:
            return None

        block = self._w3.eth.get_block(log["blockNumber"])
        ts = datetime.fromtimestamp(block["timestamp"], tz=timezone.utc).isoformat()

        event: Dict[str, Any] = {
            "type": "whale_tx",
            "ts": ts,
            "chain": self.cfg.chain,
            "token": self.cfg.token_symbol,
            "amount": float(amount),
            "notional_usd": float(notional_usd),
            "from": from_addr,
            "to": to_addr,
            "tx_hash": log["transactionHash"].hex(),
            "source": "onchain",
        }
        return event
