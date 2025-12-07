from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

from .config import OrderFlowConfig
from .models import (
    OrderFlowTrade,
    OrderFlowSnapshot,
    OrderSide,
    MarketOrderFlowState,
)

logger = logging.getLogger(__name__)


class OrderFlowEngine:
    """
    High-level order flow engine.

    Il consomme des events de trades normalisés par le BOT
    et maintient des métriques d'order flow par marché sur une fenêtre glissante.
    """

    def __init__(self, config: OrderFlowConfig):
        self.config = config
        self._markets: Dict[str, MarketOrderFlowState] = {}

        for m in (self.config.markets or []):
            self._markets[m] = MarketOrderFlowState(
                market=m,
                window_seconds=self.config.window_seconds,
            )

        logger.info(
            "OrderFlowEngine initialized (enabled=%s, markets=%s, window=%ss, whale_min_usd=%s)",
            self.config.enabled,
            self.config.markets,
            self.config.window_seconds,
            self.config.whale_min_notional_usd,
        )

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def on_normalized_event(
        self, event: Mapping[str, Any]
    ) -> Optional[OrderFlowSnapshot]:
        """
        Point d'entrée appelé par le BOT lorsqu'un event de trade normalisé est reçu.

        Retourne:
            OrderFlowSnapshot ou None si l'event est ignoré.
        """
        if not self.config.enabled:
            return None

        trade = self._to_trade(event)
        if trade is None:
            return None

        state = self._get_or_create_state(trade.market)
        self._apply_trade(state, trade)
        self._drop_outdated_trades(state, now=trade.ts)

        snapshot = self._build_snapshot(state, now=trade.ts)
        logger.debug(
            "OrderFlow snapshot %s: pressure=%s, imbalance=%.1f%%, delta=%s, total=%s",
            snapshot.market,
            snapshot.pressure,
            snapshot.imbalance_pct,
            snapshot.delta_volume,
            snapshot.total_volume,
        )
        return snapshot

    def get_latest_snapshot(self, market: str) -> Optional[OrderFlowSnapshot]:
        """
        Peut être appelé par l'Agent pour récupérer le dernier snapshot d'un marché.
        """
        state = self._markets.get(market)
        if not state or not state.trades:
            return None
        now = state.trades[-1].ts
        self._drop_outdated_trades(state, now=now)
        return self._build_snapshot(state, now=now)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _get_or_create_state(self, market: str) -> MarketOrderFlowState:
        if market not in self._markets:
            self._markets[market] = MarketOrderFlowState(
                market=market,
                window_seconds=self.config.window_seconds,
            )
        return self._markets[market]

    def _apply_trade(self, state: MarketOrderFlowState, trade: OrderFlowTrade) -> None:
        state.trades.append(trade)

        if trade.side == OrderSide.BUY:
            state.buy_volume += trade.size
            state.buy_notional += trade.notional_usd
            state.buy_trades += 1
            if trade.is_whale:
                state.whale_buy_volume += trade.notional_usd
                state.whale_trades += 1
        else:
            state.sell_volume += trade.size
            state.sell_notional += trade.notional_usd
            state.sell_trades += 1
            if trade.is_whale:
                state.whale_sell_volume += trade.notional_usd
                state.whale_trades += 1

    def _drop_outdated_trades(
        self, state: MarketOrderFlowState, now: datetime
    ) -> None:
        """
        Ne garde que les trades à l'intérieur de la fenêtre de temps configurée.
        """
        window_start = now - timedelta(seconds=state.window_seconds)

        while state.trades and state.trades[0].ts < window_start:
            old = state.trades.popleft()
            if old.side == OrderSide.BUY:
                state.buy_volume -= old.size
                state.buy_notional -= old.notional_usd
                state.buy_trades -= 1
                if old.is_whale:
                    state.whale_buy_volume -= old.notional_usd
                    state.whale_trades -= 1
            else:
                state.sell_volume -= old.size
                state.sell_notional -= old.notional_usd
                state.sell_trades -= 1
                if old.is_whale:
                    state.whale_sell_volume -= old.notional_usd
                    state.whale_trades -= 1

    def _build_snapshot(
        self, state: MarketOrderFlowState, now: datetime
    ) -> OrderFlowSnapshot:
        buy_volume = max(state.buy_volume, Decimal(0))
        sell_volume = max(state.sell_volume, Decimal(0))
        buy_notional = max(state.buy_notional, Decimal(0))
        sell_notional = max(state.sell_notional, Decimal(0))

        total_notional = buy_notional + sell_notional

        buy_vwap = (
            buy_notional / buy_volume if buy_volume > 0 and buy_notional > 0 else None
        )
        sell_vwap = (
            sell_notional / sell_volume
            if sell_volume > 0 and sell_notional > 0
            else None
        )

        delta = buy_notional - sell_notional
        total_volume = buy_notional + sell_notional

        if total_notional <= 0:
            imbalance_pct = 0.0
            pressure = "neutral"
        else:
            buy_share = float(buy_notional / total_notional * Decimal(100))
            sell_share = 100.0 - buy_share

            if (
                total_notional >= Decimal(self.config.min_window_notional_usd)
                and buy_share >= self.config.imbalance_threshold_pct
            ):
                pressure = "buy"
                imbalance_pct = buy_share
            elif (
                total_notional >= Decimal(self.config.min_window_notional_usd)
                and sell_share >= self.config.imbalance_threshold_pct
            ):
                pressure = "sell"
                imbalance_pct = sell_share
            else:
                pressure = "neutral"
                imbalance_pct = max(buy_share, sell_share)

        snapshot = OrderFlowSnapshot(
            ts=now,
            market=state.market,
            buy_volume=buy_notional,
            sell_volume=sell_notional,
            buy_trades=state.buy_trades,
            sell_trades=state.sell_trades,
            whale_buy_volume=state.whale_buy_volume,
            whale_sell_volume=state.whale_sell_volume,
            whale_trades=state.whale_trades,
            buy_vwap=buy_vwap,
            sell_vwap=sell_vwap,
            delta_volume=delta,
            total_volume=total_volume,
            imbalance_pct=imbalance_pct,
            pressure=pressure,
        )

        return snapshot

    # -------------------------------------------------------------------------
    # Event normalization
    # -------------------------------------------------------------------------

    def _to_trade(self, event: Mapping[str, Any]) -> Optional[OrderFlowTrade]:
        """
        Adapter depuis le schéma d'event normalisé du BOT vers OrderFlowTrade.

        Clés attendues (flexible, plusieurs variantes acceptées) :
          - market / symbol / pair
          - side / taker_side : "buy"/"sell" ou 1 / -1
          - price
          - size ou amount
          - notional_usd / value_usd / quote_amount_usd / notional (optionnel)
          - ts / timestamp (seconds, ms ou ISO string)
          - source / venue (optionnel)
          - tx_hash / hash (optionnel)
          - trader / wallet / address (optionnel)
        """
        try:
            market = str(
                event.get("market")
                or event.get("symbol")
                or event.get("pair")
                or ""
            )
            if not market:
                return None

            # Filtre sur les markets si configuré
            if self.config.markets and market not in self.config.markets:
                return None

            side_raw = event.get("side") or event.get("taker_side")
            side = self._parse_side(side_raw)
            if side is None:
                return None

            price = Decimal(str(event.get("price")))
            size = Decimal(str(event.get("size") or event.get("amount")))
        except (InvalidOperation, TypeError, ValueError):
            logger.debug("OrderFlow: invalid price/size in event: %s", event)
            return None

        # notional / value in USD
        notional_candidate = (
            event.get("notional_usd")
            or event.get("value_usd")
            or event.get("quote_amount_usd")
            or event.get("notional")
        )

        if notional_candidate is None:
            notional_usd = price * size
        else:
            try:
                notional_usd = Decimal(str(notional_candidate))
            except (InvalidOperation, TypeError, ValueError):
                notional_usd = price * size

        ts = self._parse_timestamp(event.get("ts") or event.get("timestamp"))

        source = event.get("source") or event.get("venue") or None
        if self.config.allowed_sources and source not in self.config.allowed_sources:
            return None

        tx_hash = event.get("tx_hash") or event.get("hash") or None
        trader = event.get("trader") or event.get("wallet") or event.get("address")

        is_whale = notional_usd >= Decimal(self.config.whale_min_notional_usd)

        return OrderFlowTrade(
            ts=ts,
            market=market,
            side=side,
            price=price,
            size=size,
            notional_usd=notional_usd,
            is_whale=is_whale,
            source=source,
            tx_hash=tx_hash,
            trader=trader,
        )

    @staticmethod
    def _parse_side(raw: Any) -> Optional[OrderSide]:
        if raw is None:
            return None
        if isinstance(raw, str):
            r = raw.lower()
            if r in ("buy", "b", "long", "1"):
                return OrderSide.BUY
            if r in ("sell", "s", "short", "-1"):
                return OrderSide.SELL
            return None
        if isinstance(raw, (int, float)):
            if raw > 0:
                return OrderSide.BUY
            if raw < 0:
                return OrderSide.SELL
        return None

    @staticmethod
    def _parse_timestamp(raw: Any) -> datetime:
        """
        Accepte :
          - datetime
          - int/float (seconds ou ms depuis epoch)
          - string ISO8601
        """
        if isinstance(raw, datetime):
            if raw.tzinfo is None:
                return raw.replace(tzinfo=timezone.utc)
            return raw.astimezone(timezone.utc)

        if isinstance(raw, (int, float)):
            # heuristique : si > 10^12 -> ms
            if raw > 1_000_000_000_000:
                sec = raw / 1000.0
            else:
                sec = raw
            return datetime.fromtimestamp(sec, tz=timezone.utc)

        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass

        # fallback: maintenant
        return datetime.now(tz=timezone.utc)
