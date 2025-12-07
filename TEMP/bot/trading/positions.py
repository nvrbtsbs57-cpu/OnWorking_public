from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from bot.core.logging import get_logger

logger = get_logger(__name__)


class PositionStatus(str, Enum):
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"
    STOPPED_OUT = "stopped_out"


class PositionEventType(str, Enum):
    TP1_HIT = "tp1_hit"
    TP2_HIT = "tp2_hit"
    RUNNER_CLOSED = "runner_closed"
    SL_HIT = "stop_loss_hit"
    TRAILING_ACTIVATED = "trailing_activated"
    TRAILING_STOP_HIT = "trailing_stop_hit"
    INFO = "info"


@dataclass
class TakeProfitConfig:
    tp1_pct: Decimal = Decimal("0.2")
    tp1_size_pct: Decimal = Decimal("0.5")
    tp2_pct: Decimal = Decimal("0.5")
    tp2_size_pct: Decimal = Decimal("0.3")
    enable_runner: bool = True
    move_sl_to_be_on_tp1: bool = True


@dataclass
class StopConfig:
    sl_pct: Decimal = Decimal("0.1")
    trailing_activation_pct: Decimal = Decimal("0.3")
    trailing_pct: Decimal = Decimal("0.15")


@dataclass
class PositionConfig:
    take_profit: TakeProfitConfig = field(default_factory=TakeProfitConfig)
    stop: StopConfig = field(default_factory=StopConfig)


@dataclass
class PositionEvent:
    position_id: str
    event_type: PositionEventType
    timestamp: datetime
    price: Decimal
    close_qty: Decimal = Decimal("0")
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    id: str
    trade_id: str

    chain: str
    symbol: str
    side: Any  # peut être un enum ou une simple string

    entry_price: Decimal
    initial_qty: Decimal
    remaining_qty: Decimal

    created_at: datetime
    status: PositionStatus = PositionStatus.OPEN

    # --- Ajouts GODMODE / Module 6 ---
    wallet_id: str = ""
    strategy_id: Optional[str] = None

    realized_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")

    # on peut garder un last_price facultatif pour l'UI, mais pas obligatoire
    last_price: Optional[Decimal] = None

    tp1_price: Optional[Decimal] = None
    tp2_price: Optional[Decimal] = None
    sl_price: Optional[Decimal] = None

    tp1_filled: bool = False
    tp2_filled: bool = False

    trailing_active: bool = False
    trailing_stop_price: Optional[Decimal] = None
    trail_reference_price: Optional[Decimal] = None

    config: PositionConfig = field(default_factory=PositionConfig)
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------------- side helpers ----------------

    @property
    def _side_str(self) -> str:
        if hasattr(self.side, "value"):
            return str(self.side.value).upper()
        return str(self.side).upper()

    @property
    def is_long(self) -> bool:
        s = self._side_str
        return s in ("BUY", "LONG")

    @property
    def is_short(self) -> bool:
        s = self._side_str
        return s in ("SELL", "SHORT")

    @staticmethod
    def _infer_is_long(side: Any) -> bool:
        if hasattr(side, "value"):
            val = str(side.value).upper()
        else:
            val = str(side).upper()
        return val in ("BUY", "LONG")

    # ---------------- construction ----------------

    @classmethod
    def from_trade(cls, trade: Any, cfg: PositionConfig) -> "Position":
        """
        Accepte n'importe quel objet avec au moins:
          - id, chain, symbol, side, price, qty, opened_at, reason, meta
        (side peut être un enum ou une string "BUY"/"SELL")

        Optionnellement:
          - wallet_id, strategy_id, fees
        """
        entry = Decimal(str(trade.price))
        qty = Decimal(str(trade.qty))

        side = getattr(trade, "side", "BUY")

        if cls._infer_is_long(side):
            tp1_price = entry * (Decimal("1") + cfg.take_profit.tp1_pct)
            tp2_price = entry * (Decimal("1") + cfg.take_profit.tp2_pct)
            sl_price = entry * (Decimal("1") - cfg.stop.sl_pct)
        else:
            tp1_price = entry * (Decimal("1") - cfg.take_profit.tp1_pct)
            tp2_price = entry * (Decimal("1") - cfg.take_profit.tp2_pct)
            sl_price = entry * (Decimal("1") + cfg.stop.sl_pct)

        pos_id = f"pos:{getattr(trade, 'id', 'unknown')}"
        opened_at = getattr(trade, "opened_at", datetime.utcnow())
        reason = getattr(trade, "reason", None)
        meta = getattr(trade, "meta", None) or {}

        wallet_id = str(getattr(trade, "wallet_id", ""))  # pour GODMODE: W0..W9
        strategy_id = getattr(trade, "strategy_id", None)

        fees_raw = getattr(trade, "fees", Decimal("0"))
        fees = Decimal(str(fees_raw))

        return cls(
            id=pos_id,
            trade_id=str(getattr(trade, "id", "unknown")),
            chain=str(getattr(trade, "chain", "")),
            symbol=str(getattr(trade, "symbol", "")),
            side=side,
            entry_price=entry,
            initial_qty=qty,
            remaining_qty=qty,
            created_at=opened_at,
            wallet_id=wallet_id,
            strategy_id=strategy_id if strategy_id is not None else None,
            total_fees=fees,
            tp1_price=tp1_price,
            tp2_price=tp2_price,
            sl_price=sl_price,
            config=cfg,
            meta={"reason": reason, **meta},
        )

    # ---------------- PNL helpers ----------------

    def _pnl_for_close(self, close_price: Decimal, close_qty: Decimal) -> Decimal:
        if close_qty <= 0:
            return Decimal("0")
        if self.is_long:
            return (close_price - self.entry_price) * close_qty
        else:
            return (self.entry_price - close_price) * close_qty

    def unrealized_pnl(self, price: Decimal) -> Decimal:
        """
        PnL latent sur la quantité restante (hors fees).
        """
        if self.remaining_qty <= 0:
            return Decimal("0")
        if self.is_long:
            return (price - self.entry_price) * self.remaining_qty
        else:
            return (self.entry_price - price) * self.remaining_qty

    def total_pnl(self, price: Decimal) -> Decimal:
        """
        PnL total = réalisé + latent.
        (tu peux ajuster pour inclure les fees si besoin)
        """
        return self.realized_pnl + self.unrealized_pnl(price)

    # ---------------- checks ----------------

    def _has_hit_tp1(self, price: Decimal) -> bool:
        if self.tp1_price is None or self.tp1_filled:
            return False
        if self.is_long:
            return price >= self.tp1_price
        else:
            return price <= self.tp1_price

    def _has_hit_tp2(self, price: Decimal) -> bool:
        if self.tp2_price is None or self.tp2_filled:
            return False
        if self.is_long:
            return price >= self.tp2_price
        else:
            return price <= self.tp2_price

    def _has_hit_sl(self, price: Decimal) -> bool:
        if self.sl_price is None:
            return False
        if self.is_long:
            return price <= self.sl_price
        else:
            return price >= self.sl_price

    def _update_trailing_reference(self, price: Decimal) -> None:
        if self.trail_reference_price is None:
            self.trail_reference_price = price
        else:
            if self.is_long and price > self.trail_reference_price:
                self.trail_reference_price = price
            elif self.is_short and price < self.trail_reference_price:
                self.trail_reference_price = price

        if self.trail_reference_price is not None:
            if self.is_long:
                self.trailing_stop_price = self.trail_reference_price * (
                    Decimal("1") - self.config.stop.trailing_pct
                )
            else:
                self.trailing_stop_price = self.trail_reference_price * (
                    Decimal("1") + self.config.stop.trailing_pct
                )

    def _should_activate_trailing(self, price: Decimal) -> bool:
        activation_pct = self.config.stop.trailing_activation_pct
        if activation_pct <= 0:
            return False

        if self.is_long:
            threshold = self.entry_price * (Decimal("1") + activation_pct)
            return price >= threshold
        else:
            threshold = self.entry_price * (Decimal("1") - activation_pct)
            return price <= threshold

    def _has_hit_trailing_stop(self, price: Decimal) -> bool:
        if not self.trailing_active or self.trailing_stop_price is None:
            return False
        if self.is_long:
            return price <= self.trailing_stop_price
        else:
            return price >= self.trailing_stop_price

    # ---------------- logique principale ----------------

    def update_with_price(self, price: Decimal, now: Optional[datetime] = None) -> List[PositionEvent]:
        if now is None:
            now = datetime.utcnow()

        events: List[PositionEvent] = []

        self.last_price = price

        if self.status in (PositionStatus.CLOSED, PositionStatus.STOPPED_OUT):
            return events

        # 1) trailing: activation + update
        if not self.trailing_active and self._should_activate_trailing(price):
            self.trailing_active = True
            self._update_trailing_reference(price)
            events.append(
                PositionEvent(
                    position_id=self.id,
                    event_type=PositionEventType.TRAILING_ACTIVATED,
                    timestamp=now,
                    price=price,
                    close_qty=Decimal("0"),
                    details={"trailing_stop_price": self.trailing_stop_price},
                )
            )
        elif self.trailing_active:
            self._update_trailing_reference(price)

        # 2) trailing stop ou SL (protection d'abord)
        if self._has_hit_trailing_stop(price):
            if self.remaining_qty > 0:
                events.append(
                    PositionEvent(
                        position_id=self.id,
                        event_type=PositionEventType.TRAILING_STOP_HIT,
                        timestamp=now,
                        price=price,
                        close_qty=self.remaining_qty,
                        details={"reason": "trailing_stop"},
                    )
                )
            return events

        if self._has_hit_sl(price):
            if self.remaining_qty > 0:
                events.append(
                    PositionEvent(
                        position_id=self.id,
                        event_type=PositionEventType.SL_HIT,
                        timestamp=now,
                        price=price,
                        close_qty=self.remaining_qty,
                        details={"reason": "stop_loss"},
                    )
                )
            return events

        # 3) TP1 / TP2
        tp_cfg = self.config.take_profit

        if self._has_hit_tp1(price):
            self.tp1_filled = True
            close_qty = (self.initial_qty * tp_cfg.tp1_size_pct).quantize(Decimal("0.00000001"))
            close_qty = min(close_qty, self.remaining_qty)
            if close_qty > 0:
                events.append(
                    PositionEvent(
                        position_id=self.id,
                        event_type=PositionEventType.TP1_HIT,
                        timestamp=now,
                        price=price,
                        close_qty=close_qty,
                        details={"reason": "tp1"},
                    )
                )
            if tp_cfg.move_sl_to_be_on_tp1:
                self.sl_price = self.entry_price

        if self._has_hit_tp2(price):
            self.tp2_filled = True
            close_qty = (self.initial_qty * tp_cfg.tp2_size_pct).quantize(Decimal("0.00000001"))
            close_qty = min(close_qty, self.remaining_qty)
            if close_qty > 0:
                events.append(
                    PositionEvent(
                        position_id=self.id,
                        event_type=PositionEventType.TP2_HIT,
                        timestamp=now,
                        price=price,
                        close_qty=close_qty,
                        details={"reason": "tp2"},
                    )
                )

        return events

    def apply_event(self, event: PositionEvent) -> None:
        # PnL réalisé sur la portion fermée
        if event.close_qty > 0:
            pnl = self._pnl_for_close(event.price, event.close_qty)
            self.realized_pnl += pnl
            self.remaining_qty -= event.close_qty
            if self.remaining_qty < 0:
                self.remaining_qty = Decimal("0")

        if event.event_type in (
            PositionEventType.SL_HIT,
            PositionEventType.TRAILING_STOP_HIT,
        ):
            self.status = PositionStatus.STOPPED_OUT
        elif self.remaining_qty == 0:
            self.status = PositionStatus.CLOSED
        elif event.event_type in (
            PositionEventType.TP1_HIT,
            PositionEventType.TP2_HIT,
        ):
            self.status = PositionStatus.PARTIALLY_CLOSED


@dataclass
class PositionManagerConfig:
    default_position_config: PositionConfig = field(default_factory=PositionConfig)


class PositionManager:
    def __init__(self, config: Optional[PositionManagerConfig] = None) -> None:
        self.config = config or PositionManagerConfig()
        self._positions: Dict[str, Position] = {}

    def open_from_trade(self, trade: Any, position_config: Optional[PositionConfig] = None) -> Position:
        cfg = position_config or self.config.default_position_config
        pos = Position.from_trade(trade, cfg)
        self._positions[pos.id] = pos
        logger.info(
            "PositionManager: nouvelle position ouverte id=%s trade_id=%s chain=%s symbol=%s side=%s entry=%s qty=%s wallet=%s strategy=%s",
            pos.id,
            pos.trade_id,
            pos.chain,
            pos.symbol,
            getattr(pos.side, "value", str(pos.side)),
            str(pos.entry_price),
            str(pos.initial_qty),
            pos.wallet_id,
            pos.strategy_id,
            extra={
                "event": "position_opened",
                "position_id": pos.id,
                "trade_id": pos.trade_id,
                "chain": pos.chain,
                "symbol": pos.symbol,
                "side": getattr(pos.side, "value", str(pos.side)),
                "entry_price": float(pos.entry_price),
                "qty": float(pos.initial_qty),
                "wallet_id": pos.wallet_id,
                "strategy_id": pos.strategy_id,
            },
        )
        return pos

    def on_price_tick(self, chain: str, symbol: str, price: Decimal, now: Optional[datetime] = None) -> List[PositionEvent]:
        if now is None:
            now = datetime.utcnow()
        events: List[PositionEvent] = []
        for pos in list(self._positions.values()):
            if pos.chain != chain or pos.symbol != symbol:
                continue
            evs = pos.update_with_price(price, now)
            events.extend(evs)
        return events

    def apply_events(self, events: List[PositionEvent]) -> None:
        for ev in events:
            pos = self._positions.get(ev.position_id)
            if not pos:
                continue
            pos.apply_event(ev)
            logger.info(
                "PositionManager: event appliqué position_id=%s type=%s close_qty=%s price=%s realized_pnl=%s remaining_qty=%s",
                ev.position_id,
                ev.event_type.value,
                str(ev.close_qty),
                str(ev.price),
                str(pos.realized_pnl),
                str(pos.remaining_qty),
                extra={
                    "event": "position_event_applied",
                    "position_id": ev.position_id,
                    "event_type": ev.event_type.value,
                    "close_qty": float(ev.close_qty),
                    "price": float(ev.price),
                    "realized_pnl": float(pos.realized_pnl),
                    "remaining_qty": float(pos.remaining_qty),
                },
            )

    def get_open_positions(
        self,
        chain: Optional[str] = None,
        symbol: Optional[str] = None,
        wallet_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
    ) -> List[Position]:
        res: List[Position] = []
        for pos in self._positions.values():
            if pos.status in (PositionStatus.CLOSED, PositionStatus.STOPPED_OUT):
                continue
            if chain is not None and pos.chain != chain:
                continue
            if symbol is not None and pos.symbol != symbol:
                continue
            if wallet_id is not None and pos.wallet_id != wallet_id:
                continue
            if strategy_id is not None and pos.strategy_id != strategy_id:
                continue
            res.append(pos)
        return res

    def get_position(self, position_id: str) -> Optional[Position]:
        return self._positions.get(position_id)

    # --- helper pratique pour ExecutionEngine / UI : fermeture manuelle ---

    def close_position_market(
        self,
        position_id: str,
        price: Decimal,
        now: Optional[datetime] = None,
        reason: str = "manual_close",
    ) -> Optional[PositionEvent]:
        """
        Ferme la position restante au prix donné (RUNNER_CLOSED).
        Retourne l'événement généré (ou None si position introuvable / déjà close).
        """
        if now is None:
            now = datetime.utcnow()

        pos = self._positions.get(position_id)
        if not pos or pos.remaining_qty <= 0:
            return None

        ev = PositionEvent(
            position_id=position_id,
            event_type=PositionEventType.RUNNER_CLOSED,
            timestamp=now,
            price=price,
            close_qty=pos.remaining_qty,
            details={"reason": reason},
        )

        pos.apply_event(ev)

        logger.info(
            "PositionManager: position fermée manuellement id=%s close_qty=%s price=%s realized_pnl=%s",
            position_id,
            str(ev.close_qty),
            str(price),
            str(pos.realized_pnl),
            extra={
                "event": "position_manual_close",
                "position_id": position_id,
                "close_qty": float(ev.close_qty),
                "price": float(price),
                "realized_pnl": float(pos.realized_pnl),
            },
        )

        return ev
