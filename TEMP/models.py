from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal, getcontext
from enum import Enum
from typing import Any, Dict, Optional
import uuid

# Haute précision pour tous les calculs prix/taille/PnL
getcontext().prec = 50


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


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    BSC = "bsc"
    SOLANA = "solana"
    ARBITRUM = "arbitrum"
    BASE = "base"


class SignalSource(str, Enum):
    STRATEGY = "strategy"
    WEBHOOK = "webhook"
    MANUAL = "manual"


# ======================================================================
# Signal multi-chain
# ======================================================================

@dataclass
class Signal:
    """
    Signal de trading multi-chain utilisé par l'Agent / ExecutionEngine.

    Nouveau champ "size_usd" (propre), mais alias "notional_usd"
    pour compatiblité avec l'ancien code (PaperTrader).
    """

    chain: str
    symbol: str
    side: TradeSide

    # Nom "propre" utilisé par le nouvel agent
    size_usd: Decimal

    token_address: Optional[str] = None
    entry_price: Optional[Decimal] = None
    leverage: Optional[Decimal] = None

    strategy_id: Optional[str] = None
    source: SignalSource = SignalSource.STRATEGY
    confidence: float = 1.0

    timestamp: datetime = field(default_factory=datetime.utcnow)
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------- Alias rétro-compatibilité : notional_usd ----------
    @property
    def notional_usd(self) -> Decimal:
        """
        Alias de compat avec l'ancien code (PaperTrader, etc.).
        Historiquement : signal.notional_usd
        Maintenant     : signal.size_usd
        """
        return self.size_usd

    @notional_usd.setter
    def notional_usd(self, value: Any) -> None:
        self.size_usd = Decimal(str(value))

    # ---------- Sérialisation ----------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": self.chain,
            "symbol": self.symbol,
            "side": self.side.value,
            # on expose les deux pour ne rien casser
            "size_usd": str(self.size_usd),
            "notional_usd": str(self.size_usd),
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
        # On accepte size_usd OU notional_usd (priorité à size_usd)
        raw_size = (
            d.get("size_usd")
            if d.get("size_usd") is not None
            else d.get("notional_usd", "0")
        )

        entry_raw = d.get("entry_price")
        leverage_raw = d.get("leverage")

        source_raw = d.get("source", "strategy")
        try:
            source_val = SignalSource(str(source_raw))
        except Exception:
            # fallback tolérant
            source_val = SignalSource.STRATEGY

        ts_raw = d.get("timestamp")
        if ts_raw:
            try:
                ts_val = datetime.fromisoformat(ts_raw)
            except Exception:
                ts_val = datetime.utcnow()
        else:
            ts_val = datetime.utcnow()

        return Signal(
            chain=d["chain"],
            symbol=d["symbol"],
            side=TradeSide(str(d.get("side", "buy")).lower()),
            size_usd=Decimal(str(raw_size) if raw_size is not None else "0"),
            token_address=d.get("token_address"),
            entry_price=Decimal(str(entry_raw)) if entry_raw is not None else None,
            leverage=Decimal(str(leverage_raw)) if leverage_raw is not None else None,
            strategy_id=d.get("strategy_id"),
            source=source_val,
            confidence=float(d.get("confidence", 1.0)),
            timestamp=ts_val,
            meta=d.get("meta", {}),
        )


# ======================================================================
# Modèle de Trade principal
# ======================================================================

@dataclass
class Trade:
    """
    Trade logique (utilisé par PaperTrader, AgentStatus, pipeline finance, etc.).

    - `notional` représente la taille en devise de cotation (souvent USD).
    - Pour rester cohérent avec les signaux memecoin, on expose des alias
      `size_usd` / `notional_usd` qui pointent tous sur `notional`.

    Rétro-compat :
    - `fee` et `status` ont des valeurs par défaut, ce qui permet à l'ancien
      code (ex: bot/trading/engine.PaperTradingEngine) d'instancier Trade
      sans les fournir explicitement.
    """

    id: str
    chain: str
    symbol: str
    side: TradeSide
    qty: Decimal
    price: Decimal
    notional: Decimal

    # created_at peut être omis dans du vieux code, on met un default propre.
    created_at: datetime = field(default_factory=datetime.utcnow)

    # meta libre pour embarquer raison, signaux bruts, PnL simu, etc.
    meta: Dict[str, Any] = field(default_factory=dict)

    # Champs ajoutés + defaults pour compatibilité
    fee: Decimal = Decimal("0")
    status: TradeStatus = TradeStatus.EXECUTED

    # ---------- Alias pratiques / compatibilité ----------
    @property
    def size_usd(self) -> Decimal:
        """
        Alias "propre" côté runtime memecoin.
        En interne, tout est stocké dans `notional`.
        """
        return self.notional

    @size_usd.setter
    def size_usd(self, value: Any) -> None:
        self.notional = Decimal(str(value))

    @property
    def notional_usd(self) -> Decimal:
        """
        Alias pour être cohérent avec `Signal.notional_usd`.
        """
        return self.notional

    @notional_usd.setter
    def notional_usd(self, value: Any) -> None:
        self.notional = Decimal(str(value))

    # ---------- Sérialisation ----------
    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["qty"] = str(self.qty)
        d["price"] = str(self.price)
        d["notional"] = str(self.notional)
        d["fee"] = str(self.fee)
        d["created_at"] = self.created_at.isoformat()

        # Tolérant si jamais quelqu'un a passé side/status en str
        side_val = self.side.value if isinstance(self.side, TradeSide) else str(self.side)
        status_val = self.status.value if isinstance(self.status, TradeStatus) else str(self.status)
        d["side"] = side_val
        d["status"] = status_val

        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Trade":
        # side robuste : accepte Enum ou str (BUY/buy/etc.)
        raw_side = d["side"]
        if isinstance(raw_side, TradeSide):
            side_val = raw_side
        else:
            side_val = TradeSide(str(raw_side).lower())

        # status robuste : accepte Enum ou str
        raw_status = d.get("status", "executed")
        if isinstance(raw_status, TradeStatus):
            status_val = raw_status
        else:
            status_val = TradeStatus(str(raw_status).lower())

        created_raw = d.get("created_at")
        if created_raw:
            try:
                created_val = datetime.fromisoformat(created_raw)
            except Exception:
                created_val = datetime.utcnow()
        else:
            created_val = datetime.utcnow()

        return Trade(
            id=d["id"],
            chain=d["chain"],
            symbol=d["symbol"],
            side=side_val,
            qty=Decimal(str(d["qty"])),
            price=Decimal(str(d["price"])),
            notional=Decimal(str(d["notional"])),
            created_at=created_val,
            meta=d.get("meta", {}),
            fee=Decimal(str(d.get("fee", "0"))),
            status=status_val,
        )

    @classmethod
    def new(
        cls,
        *,
        chain: str,
        symbol: str,
        side: TradeSide,
        qty: Decimal,
        price: Decimal,
        notional: Decimal,
        fee: Decimal = Decimal("0"),
        meta: Optional[Dict[str, Any]] = None,
    ) -> "Trade":
        """Constructeur pratique utilisé par PaperTrader (et autres moteurs)."""
        trade_id = f"{chain}:{uuid.uuid4().hex}"
        return cls(
            id=trade_id,
            chain=chain,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            notional=notional,
            fee=fee,
            status=TradeStatus.EXECUTED,
            created_at=datetime.utcnow(),
            meta=meta or {},
        )


# ======================================================================
# Positions & PnL
# ======================================================================

@dataclass
class Position:
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
    """Stats globales PnL pour le dashboard / PaperTrader."""

    currency: str
    realized: Decimal
    unrealized: Decimal
    total: Decimal
    win_rate: float
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
# Etat de l’agent (utilisé par PaperTrader / GODMODE dashboard)
# ======================================================================

@dataclass
class AgentStatus:
    mode: str = "GODMODE"
    is_running: bool = False
    last_heartbeat: Optional[datetime] = None
    last_error: Optional[str] = None

    current_blocks: Dict[str, int] = field(default_factory=dict)
    last_trade: Optional[Trade] = None
    pnl: Optional[PnLStats] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

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
            "meta": self.meta,
        }

