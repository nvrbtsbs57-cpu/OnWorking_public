from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Callable

from bot.signals import (
    RawSignal,
    ScoreBreakdown,
    ScoredSignal,
)
from .risk import RiskConfig, VolatilityBucket
from .risk_engine import RiskEngine

logger = logging.getLogger(__name__)


# ======================================================================
# Scoring
# ======================================================================

@dataclass
class ScoringConfig:
    weights: Dict[str, Decimal]  # {"flow": 0.4, "trend": 0.3, ...}
    min_confidence: Decimal
    min_total_score: Decimal


class ScoringEngine:
    """
    Combine les features des signaux avec des poids par composante
    pour produire un score [0,1].
    """

    def __init__(self, config: ScoringConfig):
        self.config = config

    def score(self, signal: RawSignal) -> ScoreBreakdown:
        component_scores: Dict[str, Decimal] = {}

        for feature in signal.features:
            # Convention simple : "flow_whale_notional" -> composante "flow"
            prefix = feature.name.split("_", 1)[0]
            base_weight = self.config.weights.get(prefix, Decimal("0"))
            if base_weight <= 0:
                continue

            contrib = feature.value * feature.weight * base_weight
            component_scores[prefix] = component_scores.get(prefix, Decimal("0")) + contrib

        total_score = sum(component_scores.values(), Decimal("0"))

        # clamp très simple (à affiner plus tard)
        if total_score < 0:
            total_score = Decimal("0")
        if total_score > 1:
            total_score = Decimal("1")

        # prise en compte de la confiance de la source
        total_score = total_score * signal.confidence

        return ScoreBreakdown(
            total_score=total_score,
            components=component_scores,
        )

    def is_acceptable(self, breakdown: ScoreBreakdown, confidence: Decimal) -> bool:
        """
        Filtre un minimum de qualité pour éviter le bruit.
        """
        if confidence < self.config.min_confidence:
            return False
        return breakdown.total_score >= self.config.min_total_score


# ======================================================================
# SignalEngine orchestration (utilisé par l'Agent)
# ======================================================================

class SignalEngine:
    """
    Orchestrateur côté agent :
    - prend des RawSignal venant des autres modules
    - applique scoring + risk
    - renvoie des ScoredSignal prêts pour l'AgentEngine / alertes / dashboard
    """

    def __init__(
        self,
        scoring_cfg: dict,
        risk_cfg: dict,
    ):
        # ---- Scoring config ----
        scoring_config = ScoringConfig(
            weights={k: Decimal(str(v)) for k, v in scoring_cfg.get("weights", {}).items()},
            min_confidence=Decimal(str(scoring_cfg.get("min_confidence", 0.4))),
            min_total_score=Decimal(str(scoring_cfg.get("min_total_score", 0.6))),
        )

        # ---- Risk config ----
        vol_buckets = [
            VolatilityBucket(
                name=b["name"],
                atr_pct_max=Decimal(str(b["atr_pct_max"])),
                risk_multiplier=Decimal(str(b["risk_multiplier"])),
            )
            for b in risk_cfg.get("volatility_buckets", [])
        ]

        risk_config = RiskConfig(
            default_risk_per_trade_pct=Decimal(str(risk_cfg.get("default_risk_per_trade_pct", 0.5))),
            max_risk_per_trade_pct=Decimal(str(risk_cfg.get("max_risk_per_trade_pct", 1.0))),
            max_global_risk_pct=Decimal(str(risk_cfg.get("max_global_risk_pct", 5.0))),
            per_market=risk_cfg.get("per_market", {}),
            volatility_buckets=vol_buckets,
        )

        self.scoring_engine = ScoringEngine(scoring_config)
        self.risk_engine = RiskEngine(risk_config)

    def process_signals(
        self,
        signals: Iterable[RawSignal],
        get_account_equity: Callable[[str], Decimal],
        get_atr_pct: Optional[Callable[[str], Optional[Decimal]]] = None,
    ) -> List[ScoredSignal]:
        """
        Point d'entrée principal.
        """
        results: List[ScoredSignal] = []

        for raw in signals:
            breakdown = self.scoring_engine.score(raw)

            if not self.scoring_engine.is_acceptable(breakdown, raw.confidence):
                logger.debug(
                    "Signal rejeté (score=%s, conf=%s, id=%s, label=%s)",
                    breakdown.total_score, raw.confidence, raw.id, raw.label,
                )
                continue

            symbol = raw.context.symbol or ""
            atr_pct = get_atr_pct(symbol) if get_atr_pct else None

            # Profil de risque : on fabrique un ScoredSignal minimal juste pour passer au RiskEngine
            tmp_scored = ScoredSignal(
                raw=raw,
                score=breakdown,
                risk=None,
                position=None,
            )
            risk_profile = self.risk_engine.build_risk_profile(tmp_scored, atr_pct)

            # Sizing
            equity = get_account_equity(raw.context.chain)
            from decimal import Decimal as _D  # pour éviter collision
            entry_price = _D(raw.meta.get("entry_price", "0"))
            position = self.risk_engine.compute_position_size(
                risk_profile=risk_profile,
                account_equity=equity,
                entry_price=entry_price,
                side=raw.side.value,
            )

            scored = ScoredSignal(
                raw=raw,
                score=breakdown,
                risk=risk_profile,
                position=position,
            )
            results.append(scored)

        return results
