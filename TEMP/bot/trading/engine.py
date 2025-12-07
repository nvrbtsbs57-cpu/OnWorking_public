# bot/trading/engine.py

from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from bot.core.logging import get_logger
from .models import Trade, TradeSide
from .store import TradeStore, TradeStoreConfig

logger = get_logger(__name__)


# ======================================================================
# TradeSignal : représentation simplifiée d'un ordre à exécuter
# ======================================================================


@dataclass
class TradeSignal:
    """
    Signal de trade générique utilisé par le PaperTradingEngine.

    - chain / symbol : marché ciblé
    - side           : BUY / SELL (TradeSide)
    - qty            : quantité (en unités de token)
    - price          : prix unitaire
    - reason         : raison courte (ex: "strategy_memecoin", "whale_inflow")
    - meta           : dict libre (strategy_id, score, etc.)
    """

    chain: str
    symbol: str
    side: TradeSide
    qty: Decimal
    price: Decimal
    reason: str
    meta: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------------
    # Construction depuis un event "whale_tx"
    # --------------------------------------------------------------

    @classmethod
    def from_event_whale(
        cls,
        ev: Dict[str, Any],
        *,
        default_symbol: str = "UNKNOWN",
        min_notional_usd: Decimal = Decimal("0"),
    ) -> Optional["TradeSignal"]:
        """
        Construit un TradeSignal à partir d'un event "whale_tx".

        Hypothèses :
        - ev["type"] ou ev["kind"] == "whale_tx"
        - ev contient :
            * notional_usd ou usd_value ou amount
            * amount / qty / size
            * direction: "inflow" / "outflow"
        Logique simple :
          - side = BUY si direction == inflow
          - side = SELL si direction == outflow
          - qty = amount
          - price ≈ notional_usd / amount
        """
        ev_type = str(ev.get("type") or ev.get("kind") or "").lower()
        if ev_type != "whale_tx":
            return None

        notional_raw = (
            ev.get("notional_usd") or ev.get("usd_value") or ev.get("amount")
        )
        try:
            notional = Decimal(str(notional_raw or "0"))
        except Exception:
            notional = Decimal("0")

        if notional < min_notional_usd:
            return None

        amount_raw = ev.get("amount") or ev.get("qty") or ev.get("size")
        try:
            amount = Decimal(str(amount_raw or "0"))
        except Exception:
            amount = Decimal("0")

        if amount <= 0:
            return None

        # prix approx = notional / amount
        try:
            price = (notional / amount).quantize(Decimal("0.00000001"))
        except Exception:
            price = Decimal("0")

        direction = str(ev.get("direction") or "inflow").lower()
        side = TradeSide.BUY if direction == "inflow" else TradeSide.SELL

        chain = str(ev.get("chain") or "unknown")
        symbol = str(
            ev.get("token_symbol")
            or ev.get("symbol")
            or ev.get("token")
            or default_symbol
        )

        reason = f"whale_{direction}"

        meta = {
            "source": ev.get("source", "whale_scanner"),
            "event_type": "whale_tx",
            "direction": direction,
            "notional_usd": float(notional),
            "amount": float(amount),
            "tx_hash": ev.get("tx_hash") or ev.get("transaction_hash"),
        }

        return cls(
            chain=chain,
            symbol=symbol,
            side=side,
            qty=amount,
            price=price,
            reason=reason,
            meta=meta,
        )


# ======================================================================
# PaperTradingEngine : moteur qui transforme les signaux en trades stockés
# ======================================================================


class PaperTradingEngine:
    """
    Moteur de paper trading GODMODE.

    - reçoit des TradeSignal (via execute_signal)
    - les transforme en Trade
    - les enregistre dans TradeStore
    """

    def __init__(
        self,
        store: Optional[TradeStore] = None,
        config: Optional[TradeStoreConfig] = None,
    ) -> None:
        if store is not None:
            self.store = store
        else:
            cfg = config or TradeStoreConfig()
            self.store = TradeStore(cfg)

        logger.info(
            "PaperTradingEngine initialisé (base_dir=%s, file=%s)",
            self.store.config.base_dir,
            self.store.config.trades_file,
            extra={
                "event": "paper_engine_init",
                "base_dir": str(self.store.config.base_dir),
                "file": str(self.store.config.trades_file),
            },
        )

    # --------------------------------------------------------------
    def execute_signal(self, signal: TradeSignal) -> Trade:
        """
        Transforme un TradeSignal en Trade et l'enregistre dans le store.
        """
        now = datetime.utcnow()
        notional = (signal.qty * signal.price).quantize(Decimal("0.00000001"))

        trade = Trade(
            id=uuid.uuid4().hex,
            chain=signal.chain,
            symbol=signal.symbol,
            side=signal.side,
            qty=signal.qty,
            price=signal.price,
            notional=notional,
            created_at=now,
            meta={
                "reason": signal.reason,
                "signal": asdict(signal),
                **(signal.meta or {}),
            },
        )

        self.store.append_trade(trade)

        logger.info(
            "PaperTradingEngine: trade exécuté %s %s qty=%s @ %s notional=%s",
            signal.side.value.upper(),
            signal.symbol,
            str(signal.qty),
            str(signal.price),
            str(notional),
            extra={
                "event": "paper_trade_executed",
                "trade_id": trade.id,
                "chain": trade.chain,
                "symbol": trade.symbol,
                "side": trade.side.value,
                "qty": float(trade.qty),
                "price": float(trade.price),
                "notional": float(trade.notional),
                "reason": signal.reason,
            },
        )

        return trade

    # --------------------------------------------------------------
    def maybe_trade_from_whale_event(
        self,
        ev: Dict[str, Any],
        *,
        min_notional_usd: Decimal,
    ) -> Optional[Trade]:
        """
        Helper pratique :

        - regarde si l'event est une whale "assez grosse"
        - si oui, construit un TradeSignal à partir de l'event
        - et exécute le trade

        (La stratégie est volontairement simple : BUY sur inflow, SELL sur outflow.)
        """
        sig = TradeSignal.from_event_whale(
            ev,
            default_symbol="UNKNOWN",
            min_notional_usd=min_notional_usd,
        )
        if sig is None:
            return None

        return self.execute_signal(sig)
