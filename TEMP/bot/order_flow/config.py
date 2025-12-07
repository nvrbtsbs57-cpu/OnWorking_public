from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Any, Optional


@dataclass
class OrderFlowConfig:
    enabled: bool = True

    # Markets à suivre, ex: ["ETH-USDC", "BTC-USDT"]
    markets: List[str] | None = None

    # Fenêtre de calcul (en secondes)
    window_seconds: int = 60

    # Taille minimale (en USD) pour considérer un trade comme "whale"
    whale_min_notional_usd: float = 50_000.0

    # Volume minimal pour considérer un déséquilibre significatif (en USD)
    min_window_notional_usd: float = 10_000.0

    # Seuil d’imbalance en pourcentage pour dire "fort buy/sell pressure"
    imbalance_threshold_pct: float = 60.0  # ex: 60% du volume côté buy ou sell

    # Optionnel : sources à inclure (dex, cex, onchain, etc.)
    allowed_sources: List[str] | None = None  # None = tout

    @classmethod
    def from_dict(cls, raw: Dict[str, Any] | None) -> "OrderFlowConfig":
        raw = raw or {}

        return cls(
            enabled=bool(raw.get("enabled", True)),
            markets=raw.get("markets") or [],
            window_seconds=int(raw.get("window_seconds", 60)),
            whale_min_notional_usd=float(
                raw.get("whale_min_notional_usd", 50_000.0)
            ),
            min_window_notional_usd=float(
                raw.get("min_window_notional_usd", 10_000.0)
            ),
            imbalance_threshold_pct=float(
                raw.get("imbalance_threshold_pct", 60.0)
            ),
            allowed_sources=raw.get("allowed_sources") or None,
        )
