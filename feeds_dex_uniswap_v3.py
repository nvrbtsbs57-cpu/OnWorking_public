from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from datetime import datetime, timezone

from web3 import Web3

logger = logging.getLogger(__name__)


@dataclass
class UniswapV3Config:
    rpc_url: str
    pool_address: str  # adresse du pool Uniswap V3
    min_notional_usd: float = 1_000.0  # filtre pour ignorer les petites tailles
    token0_decimals: int = 6   # USDC
    token1_decimals: int = 18  # WETH
    market: str = "ETH-USDC"


@dataclass
class UniswapV3TradeFeed:
    """
    Feed DEX réel sur un pool Uniswap V3.

    - écoute les événements Swap du pool
    - transforme ça en events normalisés type "trade"
      compatibles avec OrderFlowEngine + AgentEngine
    """

    cfg: UniswapV3Config
    _w3: Web3 = field(init=False)
    _pool: str = field(init=False)
    _swap_topic: str = field(init=False)
    _last_block: Optional[int] = field(default=None, init=False)

    def __post_init__(self):
        self._w3 = Web3(Web3.HTTPProvider(self.cfg.rpc_url))
        self._pool = Web3.to_checksum_address(self.cfg.pool_address)
        self._swap_topic = Web3.keccak(
            text="Swap(address,address,int256,int256,uint160,uint128,int24)"
        ).hex()

        try:
            current_block = self._w3.eth.block_number
        except Exception as e:
            logger.error("UniswapV3TradeFeed: cannot get latest block: %s", e)
            current_block = None

        self._last_block = current_block
        logger.info(
            "UniswapV3TradeFeed initialized (pool=%s, start_block=%s)",
            self._pool,
            self._last_block,
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def poll(self) -> List[Dict[str, Any]]:
        """
        Récupère les nouveaux swaps depuis le dernier block traité.
        Retourne une liste d'events normalisés ("trade").
        """
        events: List[Dict[str, Any]] = []

        try:
            latest = self._w3.eth.block_number
        except Exception as e:
            logger.error("UniswapV3TradeFeed: error getting block_number: %s", e)
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
                    "address": self._pool,
                    "topics": [self._swap_topic],
                }
            )
        except Exception as e:
            logger.error(
                "UniswapV3TradeFeed: get_logs error (blocks %s-%s): %s",
                from_block,
                to_block,
                e,
            )
            # on met à jour pour éviter de re-spammer
            self._last_block = latest
            return events

        for log in logs:
            try:
                ev = self._parse_swap_log(log)
                if ev is not None:
                    events.append(ev)
            except Exception as e:
                logger.debug("UniswapV3TradeFeed: failed to parse log: %s", e)

        self._last_block = latest
        if events:
            logger.info(
                "UniswapV3TradeFeed: %s swaps parsed from blocks %s-%s",
                len(events),
                from_block,
                to_block,
            )
        return events

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _parse_swap_log(self, log: Any) -> Optional[Dict[str, Any]]:
        """
        Décode un event Swap du pool Uniswap V3.

        data encode:
          int256 amount0,
          int256 amount1,
          uint160 sqrtPriceX96,
          uint128 liquidity,
          int24 tick
        """
        data_bytes = bytes.fromhex(log["data"][2:])
        decoded = self._w3.codec.decode(
            ["int256", "int256", "uint160", "uint128", "int24"],
            data_bytes,
        )
        amount0, amount1, sqrt_price_x96, liquidity, tick = decoded

        # On travaille en valeurs absolues
        usdc_amount = abs(amount0) / (10 ** self.cfg.token0_decimals)
        eth_amount = abs(amount1) / (10 ** self.cfg.token1_decimals)

        if usdc_amount <= 0 or eth_amount <= 0:
            return None

        notional_usd = usdc_amount  # 1 USDC ~ 1 USD

        if notional_usd < self.cfg.min_notional_usd:
            return None

        # Prix approx : USDC / ETH
        price = notional_usd / eth_amount if eth_amount > 0 else 0.0

        # Déterminer le sens du trade:
        # convention approximative : si amount1 < 0 -> user achète ETH (buy)
        side = "buy" if amount1 < 0 else "sell"

        block = self._w3.eth.get_block(log["blockNumber"])
        ts = datetime.fromtimestamp(block["timestamp"], tz=timezone.utc).isoformat()

        event: Dict[str, Any] = {
            "type": "trade",
            "ts": ts,
            "market": self.cfg.market,
            "side": side,
            "price": float(price),
            "size": float(eth_amount),
            "notional_usd": float(notional_usd),
            "source": "dex",
            "tx_hash": log["transactionHash"].hex(),
            "trader": None,  # pas trivial à dériver ici
        }
        return event
