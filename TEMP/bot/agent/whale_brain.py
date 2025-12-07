from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

from bot.bot_core.normalizer.whale_normalizer import (
    WhalePressureSnapshot,
    WhaleSignal,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Dataclasses — Décision Agent
# ============================================================================

@dataclass
class WhaleDecision:
    """
    Décision envoyée par le module whales au AgentEngine.
    """
    chain: str
    block: int

    label: str            # ex: "bullish_whale_inflow", "bearish_distrib", ...
    confidence: float     # 0-1
    pressure: float       # 0-100
    context: Dict         # toutes les infos utiles

    def to_json(self) -> Dict:
        return asdict(self)


# ============================================================================
# Whale Brain (Agent)
# ============================================================================

class WhaleBrain:
    """
    GODMODE Whale Brain
    - Prend un snapshot normalisé
    - Convertit en décisions lisibles pour l’agent
    - Score la confiance
    - Produit un label propre et clair
    """

    def __init__(self, chain: str):
        self.chain = chain

        # historique des décisions
        self._decisions: List[WhaleDecision] = []

    # ----------------------------------------------------------------------
    def process(self, snap: WhalePressureSnapshot) -> WhaleDecision:
        """
        Le coeur : prend un WhalePressureSnapshot et génère une décision agent.
        """

        if snap.total_usd <= 0 or snap.whale_count == 0:
            decision = self._make_decision(
                snap,
                label="no_whale_activity",
                confidence=0.0,
            )
            self._store(decision)
            return decision

        label = self._classify(snap)
        confidence = self._compute_confidence(snap)

        decision = self._make_decision(
            snap,
            label=label,
            confidence=confidence,
        )

        self._store(decision)
        return decision

    # ----------------------------------------------------------------------
    def _classify(self, snap: WhalePressureSnapshot) -> str:
        """
        Détermine un label propre pour l’agent.
        # Exemples :
        - bullish_whale_inflow
        - bearish_whale_outflow
        - extreme_whale_activity
        - accumulation_phase
        - distribution_phase
        """

        buy_p = snap.buy_pressure
        sell_p = snap.sell_pressure
        score = snap.pressure_score

        # Activité extrême
        if score >= 85:
            if buy_p > sell_p:
                return "extreme_whale_inflow"
            elif sell_p > buy_p:
                return "extreme_whale_outflow"
            return "extreme_whale_activity"

        # Accumulation / distribution
        if buy_p >= 55 and buy_p > sell_p:
            return "accumulation_phase"

        if sell_p >= 55 and sell_p > buy_p:
            return "distribution_phase"

        # Activité directionnelle
        if buy_p > sell_p:
            return "bullish_whale_inflow"

        if sell_p > buy_p:
            return "bearish_whale_outflow"

        # Activité neutre / faible
        return "neutral_whale_activity"

    # ----------------------------------------------------------------------
    def _compute_confidence(self, snap: WhalePressureSnapshot) -> float:
        """
        Déduit la confiance d’un score 0-100 → 0-1.
        Intègre aussi le déséquilibre inflow/outflow.
        """

        base = snap.pressure_score / 100

        # plus les whales agissent dans une seule direction → plus forte confiance
        directional_strength = abs(snap.buy_pressure - snap.sell_pressure) / 100

        confidence = min(1.0, base * 0.7 + directional_strength * 0.3)

        return round(confidence, 4)

    # ----------------------------------------------------------------------
    def _make_decision(self, snap: WhalePressureSnapshot, label: str, confidence: float) -> WhaleDecision:
        """
        Fabrique l’objet final envoyé à l’agent.
        """

        context = {
            "pressure_score": snap.pressure_score,
            "buy_pressure": snap.buy_pressure,
            "sell_pressure": snap.sell_pressure,
            "inflow_usd": snap.inflow_usd,
            "outflow_usd": snap.outflow_usd,
            "netflow_usd": snap.netflow_usd,
            "whale_count": snap.whale_count,
            "total_usd": snap.total_usd,
            "raw_signals": [s.signal_type for s in snap.signals],
        }

        decision = WhaleDecision(
            chain=self.chain,
            block=snap.block,
            label=label,
            confidence=confidence,
            pressure=snap.pressure_score,
            context=context,
        )

        logger.info({
            "module": "WhaleBrain",
            "chain": self.chain,
            "block": snap.block,
            "decision": decision.to_json()
        })

        return decision

    # ----------------------------------------------------------------------
    def _store(self, decision: WhaleDecision) -> None:
        """
        Enregistre les décisions en historique local.
        """
        self._decisions.append(decision)
        if len(self._decisions) > 1000:
            self._decisions.pop(0)

    # ----------------------------------------------------------------------
    def get_history(self) -> List[WhaleDecision]:
        """Retourne l'historique complet des décisions whales."""
        return list(self._decisions)
