from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.signals import RiskProfile, PositionSize, ScoredSignal
from .risk import RiskConfig


class RiskEngine:
    """
    Convertit un ScoredSignal en profil de risque + sizing.
    """

    def __init__(self, config: RiskConfig):
        self.config = config

    def _select_bucket(self, atr_pct: Optional[Decimal]) -> Decimal:
        """
        Sélectionne un multiplicateur de risque en fonction du bucket de volatilité.
        """
        if atr_pct is None:
            return Decimal("1")

        for bucket in self.config.volatility_buckets:
            if atr_pct <= bucket.atr_pct_max:
                return bucket.risk_multiplier

        # au-delà du dernier bucket -> très risqué
        return Decimal("0.5")

    def build_risk_profile(self, scored: ScoredSignal, atr_pct: Optional[Decimal]) -> RiskProfile:
        symbol = scored.raw.context.symbol or ""
        market_cfg = self.config.per_market.get(symbol, {})

        risk_per_trade_pct = market_cfg.get(
            "risk_per_trade_pct",
            self.config.default_risk_per_trade_pct,
        )

        if risk_per_trade_pct > self.config.max_risk_per_trade_pct:
            risk_per_trade_pct = self.config.max_risk_per_trade_pct

        max_leverage = Decimal(str(market_cfg.get("max_leverage", 10)))
        max_notional = Decimal("0")  # à renseigner plus tard avec le module wallet / equity

        risk_multiplier = self._select_bucket(atr_pct)

        risk_level = "medium"
        if risk_multiplier > 1:
            risk_level = "low"
        elif risk_multiplier < 1:
            risk_level = "high"

        # paramètres simplifiés pour l’instant
        stop_distance_pct = Decimal("0.01")
        take_profit_rr = Decimal("3")

        return RiskProfile(
            risk_level=risk_level,
            max_leverage=max_leverage * risk_multiplier,
            max_notional=max_notional,
            stop_distance_pct=stop_distance_pct,
            take_profit_rr=take_profit_rr,
        )

    def compute_position_size(
        self,
        risk_profile: RiskProfile,
        account_equity: Decimal,
        entry_price: Decimal,
        side: str,
        risk_per_trade_pct: Optional[Decimal] = None,
    ) -> PositionSize:
        """
        Sizing simple basé sur % de l'équity et distance du stop.
        """
        if risk_per_trade_pct is None:
            # fallback 1% du portefeuille
            risk_per_trade_pct = Decimal("1")

        risk_amount = account_equity * (risk_per_trade_pct / Decimal("100"))

        stop_distance = entry_price * risk_profile.stop_distance_pct
        if stop_distance <= 0:
            quantity = Decimal("0")
        else:
            quantity = risk_amount / stop_distance

        notional = quantity * entry_price

        if side == "long":
            stop_price = entry_price - stop_distance
            tp_distance = stop_distance * risk_profile.take_profit_rr
            take_profit_price = entry_price + tp_distance
        else:
            stop_price = entry_price + stop_distance
            tp_distance = stop_distance * risk_profile.take_profit_rr
            take_profit_price = entry_price - tp_distance

        return PositionSize(
            account_equity=account_equity,
            risk_per_trade_pct=risk_per_trade_pct,
            notional=notional,
            quantity=quantity,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
        )
