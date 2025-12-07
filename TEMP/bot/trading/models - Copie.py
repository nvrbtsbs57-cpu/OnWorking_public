# bot/trading/models.py

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal, getcontext
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

getcontext().prec = 50  # haute précision pour les prix/chiffres


# ======================================================================
# Enums / Types de base
# ======================================================================

class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(str, Enum):
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# ======================================================================
# Enums / modèles pour les signaux (multi-chain)
# ======================================================================

class Chain(str, Enum):
    """
    Chains supportées par le bot.

    On continue à stocker les chaînes en `str` dans les modèles (Trade,
    Position, AgentStatus, etc.) pour rester 100% compatible, mais on
    peut utiliser ces constantes partout dans le code.
    """
    ETHEREUM = "ethereum"
    BSC = "bsc"
    SOLANA = "solana"
    ARBITRUM = "arbitrum"
    BASE = "base"


class SignalSource(str, Enum):
    """Origine d'un signal (stratégie interne, webhook externe, manuel, etc.)."""
    STRATEGY = "strategy"
    WEBHOOK = "webhook"
    MANUAL = "manual"


@dataclass
class Signal:
    """
    Signal de trading multi-chain utilisé par l'Agent / ExecutionEngine.

    - `chain` : nom de la chain (utiliser de préférence Chain.XXX.value)
    - `symbol` : ticker lisible (ex: "WIF", "ETH", "SOL")
    - `side` : BUY / SELL (même enum que TradeSide pour simplifier l'exécution)
    - `size_usd` : taille en devise quote (souvent USDC/USDT)
    """

    chain: str                # ex: "ethereum", "bsc", "solana"
    symbol: str               # ex: "WIF", "ETH", "SOL"
    side: TradeSide           # BUY / SELL
    size_usd: Decimal         # taille en USD (ou autre quote)

    # Détails optionnels
    token_address: Optional[str] = None   # EVM: adresse du token
    entry_price: Optional[Decimal] = None
    leverage: Optional[Decimal] = None

    strategy_id: Optional[str] = None
    source: SignalSource = SignalSource.STRATEGY
    confidence: float = 1.0              # 0.0 – 1.0

    timestamp: datetime = field(default_factory=datetime.utcnow)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialisation JSON-safe pour stockage / API / dashboard."""
        return {
            "chain": self.chain,
            "symbol": self.symbol,
            "side": self.side.value,
            "size_usd": str(self.size_usd),
            "token_address": self.token_address,
            "entry_price": str(self.entry_price) if self.entry_price is not None else None,
            "leverage": str(self.leverage) if self.leverage is not None else None,
            "strategy_id": self.strategy_id,
            "source": self.source.value,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
            "meta": self.meta,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Signal":
        """Reconstruction à partir d'un dict (API, DB, etc.)."""
        return Signal(
            chain=d["chain"],
            symbol=d["symbol"],
            side=TradeSide(d.get("side", "buy")),
            size_usd=Decimal(d.get("size_usd", "0")),
            token_address=d.get("token_address"),
            entry_price=Decimal(d["entry_price"]) if d.get("entry_price") is not None else None,
            leverage=Decimal(d["leverage"]) if d.get("leverage") is not None else None,
            strategy_id=d.get("strategy_id"),
            source=SignalSource(d.get("source", "strategy")),
            confidence=float(d.get("confidence", 1.0)),
            timestamp=datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else datetime.utcnow(),
            meta=d.get("meta", {}),
        )


# ======================================================================
# Modèles principaux
# ======================================================================

@dataclass
class Trade:
    """Trade virtuel (paper trade) exécuté par le bot."""

    id: str                  # ex: "eth:0xabc123-1747988890"
    chain: str               # "ethereum", "arbitrum", etc.
    symbol: str              # "ETH", "WBTC", "USDC", ...
    side: TradeSide
    qty: Decimal             # quantité de l'asset (par ex. ETH)
    price: Decimal           # prix par unité, dans la devise de PnL (ex: USDC)
    notional: Decimal        # qty * price
    fee: Decimal             # frais, dans la même devise que notional
    status: TradeStatus
    created_at: datetime
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # sérialisation safe
        d["qty"] = str(self.qty)
        d["price"] = str(self.price)
        d["notional"] = str(self.notional)
        d["fee"] = str(self.fee)
        d["created_at"] = self.created_at.isoformat()
        d["side"] = self.side.value
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Trade":
        return Trade(
            id=d["id"],
            chain=d["chain"],
            symbol=d["symbol"],
            side=TradeSide(d["side"]),
            qty=Decimal(d["qty"]),
            price=Decimal(d["price"]),
            notional=Decimal(d["notional"]),
            fee=Decimal(d.get("fee", "0")),
            status=TradeStatus(d.get("status", "executed")),
            created_at=datetime.fromisoformat(d["created_at"]),
            meta=d.get("meta", {}),
        )


@dataclass
class PositionLot:
    """Lot interne pour calcul FIFO des PnL."""

    qty: Decimal
    entry_price: Decimal
    created_at: datetime


@dataclass
class Position:
    """Position agrégée par (chain, symbol)."""

    chain: str
    symbol: str
    total_qty: Decimal
    avg_entry_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    last_update: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": self.chain,
            "symbol": self.symbol,
            "total_qty": str(self.total_qty),
            "avg_entry_price": str(self.avg_entry_price),
            "unrealized_pnl": str(self.unrealized_pnl),
            "realized_pnl": str(self.realized_pnl),
            "last_update": self.last_update.isoformat(),
        }


@dataclass
class PnLStats:
    """Stats globales PnL pour le dashboard."""

    currency: str                     # ex: "USDC"
    realized: Decimal
    unrealized: Decimal
    total: Decimal
    win_rate: float                   # 0.0 – 1.0
    nb_trades: int
    nb_winners: int
    nb_losers: int
    updated_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "currency": self.currency,
            "realized": str(self.realized),
            "unrealized": str(self.unrealized),
            "total": str(self.total),
            "win_rate": self.win_rate,
            "nb_trades": self.nb_trades,
            "nb_winners": self.nb_winners,
            "nb_losers": self.nb_losers,
            "updated_at": self.updated_at.isoformat(),
        }


# ======================================================================
# État de l’agent pour le GODMODE dashboard
# ======================================================================

@dataclass
class AgentStatus:
    mode: str                           # "GODMODE"
    is_running: bool
    last_heartbeat: Optional[datetime]
    last_error: Optional[str] = None

    # par chain: block courant, etc.
    current_blocks: Dict[str, int] = field(default_factory=dict)

    # dernier trade exécuté
    last_trade: Optional[Trade] = None

    # stats PnL agrégées (devise quote unique, ex: USDC)
    pnl: Optional[PnLStats] = None

    # champs libres pour le dashboard
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "is_running": self.is_running,
            "last_heartbeat": self.last_heartbeat.isoformat()
            if self.last_heartbeat else None,
            "last_error": self.last_error,
            "current_blocks": self.current_blocks,
            "last_trade": self.last_trade.to_dict() if self.last_trade else None,
            "pnl": self.pnl.to_dict() if self.pnl else None,
            "extra": self.extra,
        }
