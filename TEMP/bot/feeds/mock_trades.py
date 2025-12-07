from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Any
from datetime import datetime, timezone


@dataclass
class MarketParams:
    base_price: float
    volatility_pct: float = 0.01  # 1% variation
    min_notional_usd: float = 1_000.0
    max_notional_usd: float = 200_000.0


@dataclass
class MockTradeFeed:
    """
    Génère des trades mock pour tester OrderFlow + Agent,
    sans dépendre d'une vraie source (DEX/CEX).
    """

    markets: Dict[str, MarketParams]
    interval_seconds: float = 1.0  # intervalle moyen entre deux trades
    last_emit_ts: float = field(default_factory=lambda: 0.0)

    def maybe_generate_trades(self) -> List[Dict[str, Any]]:
        """
        À appeler régulièrement dans une boucle.
        Ne génère des trades que si assez de temps s'est écoulé.
        Retourne une liste d'events normalisés pour OrderFlow/Agent.
        """
        now = time.time()
        trades: List[Dict[str, Any]] = []

        if now - self.last_emit_ts < self.interval_seconds:
            return trades

        self.last_emit_ts = now

        # On génère 1 à 3 trades aléatoires à chaque tick
        num_trades = random.randint(1, 3)

        for _ in range(num_trades):
            market = random.choice(list(self.markets.keys()))
            params = self.markets[market]

            price = self._random_price(params)
            notional_usd = self._random_notional(params)
            size = notional_usd / price if price > 0 else 0.0

            side = random.choice(["buy", "sell"])

            event = {
                "type": "trade",
                "ts": datetime.now(tz=timezone.utc).isoformat(),
                "market": market,
                "side": side,
                "price": price,
                "size": size,
                "notional_usd": notional_usd,
                "source": "mock",
                "tx_hash": None,
                "trader": None,
            }
            trades.append(event)

        return trades

    def _random_price(self, params: MarketParams) -> float:
        base = params.base_price
        # petit random walk autour du prix de base
        move = (random.random() - 0.5) * 2.0 * params.volatility_pct
        return max(0.01, base * (1.0 + move))

    def _random_notional(self, params: MarketParams) -> float:
        return random.uniform(params.min_notional_usd, params.max_notional_usd)
