from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Deque, Optional
from collections import deque


class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class OrderFlowTrade:
    """
    Représente un trade unique vu par l'engine d'order flow.
    """

    ts: datetime
    market: str               # ex: "ETH-USDC"
    side: OrderSide           # buy / sell (aggressor)
    price: Decimal
    size: Decimal             # base token size
    notional_usd: Decimal     # size * price in USD
    is_whale: bool = False
    source: Optional[str] = None  # "dex", "cex", "onchain", etc.
    tx_hash: Optional[str] = None
    trader: Optional[str] = None  # address ou account id


@dataclass
class OrderFlowSnapshot:
    """
    Vue agrégée de l'order flow sur une fenêtre glissante.
    """

    ts: datetime
    market: str

    # volumes notionnels (USD) acheteur / vendeur
    buy_volume: Decimal
    sell_volume: Decimal

    # nombre de trades
    buy_trades: int
    sell_trades: int

    # whales
    whale_buy_volume: Decimal
    whale_sell_volume: Decimal
    whale_trades: int

    # prix moyens pondérés
    buy_vwap: Optional[Decimal] = None
    sell_vwap: Optional[Decimal] = None

    # métriques dérivées
    delta_volume: Decimal = Decimal(0)
    total_volume: Decimal = Decimal(0)
    imbalance_pct: float = 0.0
    pressure: str = "neutral"  # "buy", "sell", "neutral"

    def to_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "market": self.market,
            "buy_volume": str(self.buy_volume),
            "sell_volume": str(self.sell_volume),
            "buy_trades": self.buy_trades,
            "sell_trades": self.sell_trades,
            "whale_buy_volume": str(self.whale_buy_volume),
            "whale_sell_volume": str(self.whale_sell_volume),
            "whale_trades": self.whale_trades,
            "buy_vwap": str(self.buy_vwap) if self.buy_vwap is not None else None,
            "sell_vwap": str(self.sell_vwap) if self.sell_vwap is not None else None,
            "delta_volume": str(self.delta_volume),
            "total_volume": str(self.total_volume),
            "imbalance_pct": self.imbalance_pct,
            "pressure": self.pressure,
        }


@dataclass
class MarketOrderFlowState:
    """
    État interne pour une fenêtre glissante sur un marché.
    """

    market: str
    window_seconds: int

    trades: Deque[OrderFlowTrade] = field(default_factory=deque)

    # agrégats
    buy_volume: Decimal = Decimal(0)
    sell_volume: Decimal = Decimal(0)
    buy_notional: Decimal = Decimal(0)
    sell_notional: Decimal = Decimal(0)
    buy_trades: int = 0
    sell_trades: int = 0

    whale_buy_volume: Decimal = Decimal(0)
    whale_sell_volume: Decimal = Decimal(0)
    whale_trades: int = 0

    def reset(self) -> None:
        self.trades.clear()
        self.buy_volume = Decimal(0)
        self.sell_volume = Decimal(0)
        self.buy_notional = Decimal(0)
        self.sell_notional = Decimal(0)
        self.buy_trades = 0
        self.sell_trades = 0
        self.whale_buy_volume = Decimal(0)
        self.whale_sell_volume = Decimal(0)
        self.whale_trades = 0
