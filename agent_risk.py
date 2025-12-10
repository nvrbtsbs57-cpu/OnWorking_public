from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List


@dataclass
class VolatilityBucket:
    """
    Bucket de volatilité basé sur ATR% du marché.
    """
    name: str
    atr_pct_max: Decimal
    risk_multiplier: Decimal


@dataclass
class RiskConfig:
    """
    Configuration globale du risk engine.
    """
    default_risk_per_trade_pct: Decimal
    max_risk_per_trade_pct: Decimal
    max_global_risk_pct: Decimal

    # ex: {"BTCUSDT": {"risk_per_trade_pct": 0.5, "max_leverage": 10}}
    per_market: Dict[str, Dict[str, Decimal]]
    volatility_buckets: List[VolatilityBucket]
