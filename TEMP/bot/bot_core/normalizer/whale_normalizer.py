from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from decimal import Decimal, getcontext
from typing import Any, Dict, List, Optional

from bot.bot_core.indexer.whale_scanner import BlockWhaleActivity, WhaleTransfer

getcontext().prec = 50  # haute précision pour les montants

logger = logging.getLogger(__name__)


# ============================================================================
# Config / Dataclasses
# ============================================================================

@dataclass
class WhaleNormalizerConfig:
    """
    Configuration du normaliseur de whales.
    Tout est ajustable via le config.json plus tard si besoin.
    """
    min_block_usd: float = 50_000.0        # en dessous = peu de signal
    high_pressure_threshold: float = 75.0  # au-dessus = signal fort
    medium_pressure_threshold: float = 40.0
    smoothing_alpha: float = 0.3           # lissage EMA
    max_history: int = 500                 # historique max par chain


@dataclass
class WhaleSignal:
    """
    Signal individuel utilisable par l'agent.
    """
    chain: str
    block: int
    signal_type: str        # ex: "whale_buy", "whale_sell", "accumulation", "distribution"
    direction: str          # "buy" / "sell" / "neutral"
    pressure_score: float   # 0-100
    total_usd: float
    whale_count: int
    netflow_usd: float
    inflow_usd: float
    outflow_usd: float
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WhalePressureSnapshot:
    """
    Vue agrégée de la pression whales sur un bloc.
    """
    chain: str
    block: int

    pressure_score: float       # 0-100 global
    buy_pressure: float         # 0-100
    sell_pressure: float        # 0-100

    inflow_usd: float
    outflow_usd: float
    netflow_usd: float

    whale_count: int
    total_usd: float

    signals: List[WhaleSignal] = field(default_factory=list)


# ============================================================================
# Whale Normalizer
# ============================================================================

class WhaleNormalizer:
    """
    GODMODE Whale Normalizer

    Rôle :
    - Prend un BlockWhaleActivity (indexer)
    - Calcule inflow / outflow / netflow
    - Génère un score 0-100 de "whale pressure"
    - Produit des WhaleSignal structurés pour l'agent
    """

    def __init__(
        self,
        chain: str,
        config: Optional[WhaleNormalizerConfig] = None,
    ):
        self.chain = chain
        self.config = config or WhaleNormalizerConfig()

        # état interne pour le lissage
        self._last_pressure_score: Decimal = Decimal(0)
        self._last_buy_pressure: Decimal = Decimal(0)
        self._last_sell_pressure: Decimal = Decimal(0)

        # historique (optionnel, peut servir pour des features avancées)
        self._history: List[WhalePressureSnapshot] = []

    # ----------------------------------------------------------------------
    def normalize_block(self, activity: BlockWhaleActivity) -> WhalePressureSnapshot:
        """
        Point d'entrée principal :
        prend un BlockWhaleActivity (déjà filtré en whales)
        et renvoie un WhalePressureSnapshot normalisé.
        """

        if not activity.transfers:
            snapshot = WhalePressureSnapshot(
                chain=self.chain,
                block=activity.block,
                pressure_score=0.0,
                buy_pressure=0.0,
                sell_pressure=0.0,
                inflow_usd=0.0,
                outflow_usd=0.0,
                netflow_usd=0.0,
                whale_count=0,
                total_usd=0.0,
                signals=[],
            )
            self._push_history(snapshot)
            return snapshot

        inflow_usd, outflow_usd = self._compute_flows(activity.transfers)
        netflow_usd = inflow_usd - outflow_usd

        total_usd = float(activity.total_usd or (inflow_usd + outflow_usd))

        base_pressure = self._compute_base_pressure(total_usd)
        buy_pressure_raw, sell_pressure_raw = self._compute_directional_pressure(
            inflow_usd, outflow_usd, base_pressure
        )

        # lissage EMA simple
        buy_pressure = self._smooth(self._last_buy_pressure, buy_pressure_raw)
        sell_pressure = self._smooth(self._last_sell_pressure, sell_pressure_raw)
        pressure_score = self._smooth(self._last_pressure_score, base_pressure)

        # mise à jour de l'état
        self._last_buy_pressure = buy_pressure
        self._last_sell_pressure = sell_pressure
        self._last_pressure_score = pressure_score

        # génération des signaux dérivés
        signals = self._build_signals(
            block=activity.block,
            inflow_usd=inflow_usd,
            outflow_usd=outflow_usd,
            netflow_usd=netflow_usd,
            total_usd=total_usd,
            whale_count=len(activity.transfers),
            pressure_score=float(pressure_score),
            buy_pressure=float(buy_pressure),
            sell_pressure=float(sell_pressure),
        )

        snapshot = WhalePressureSnapshot(
            chain=self.chain,
            block=activity.block,
            pressure_score=float(pressure_score),
            buy_pressure=float(buy_pressure),
            sell_pressure=float(sell_pressure),
            inflow_usd=float(inflow_usd),
            outflow_usd=float(outflow_usd),
            netflow_usd=float(netflow_usd),
            whale_count=len(activity.transfers),
            total_usd=total_usd,
            signals=signals,
        )

        self._push_history(snapshot)

        logger.info({
            "module": "WhaleNormalizer",
            "chain": self.chain,
            "block": activity.block,
            "pressure_score": float(pressure_score),
            "buy_pressure": float(buy_pressure),
            "sell_pressure": float(sell_pressure),
            "inflow_usd": float(inflow_usd),
            "outflow_usd": float(outflow_usd),
            "netflow_usd": float(netflow_usd),
            "whale_count": len(activity.transfers),
            "total_usd": total_usd,
            "signals": [asdict(s) for s in signals],
        })

        return snapshot

    # ----------------------------------------------------------------------
    @staticmethod
    def _compute_flows(transfers: List[WhaleTransfer]) -> tuple[Decimal, Decimal]:
        """
        Calcule les flux USD entrants / sortants à partir des transfers.
        direction 'inflow' => inflow_usd
        direction 'outflow' => outflow_usd
        """
        inflow = Decimal(0)
        outflow = Decimal(0)

        for t in transfers:
            usd = Decimal(str(t.usd_value))
            if t.direction == "inflow":
                inflow += usd
            else:
                outflow += usd

        return inflow, outflow

    # ----------------------------------------------------------------------
    def _compute_base_pressure(self, total_usd: float) -> Decimal:
        """
        Base du score de pression :
        - Compare le volume whale du bloc au seuil min_block_usd.
        - Clamp entre 0 et 100.
        """
        if total_usd <= 0:
            return Decimal(0)

        ratio = Decimal(str(total_usd)) / Decimal(str(self.config.min_block_usd))
        # plus le ratio est grand, plus le score sature vers 100
        base = min(Decimal(100), ratio * Decimal(40))  # 1x = 40, 2x = 80, >2.5x ≈ 100
        return base

    # ----------------------------------------------------------------------
    def _compute_directional_pressure(
        self,
        inflow_usd: Decimal,
        outflow_usd: Decimal,
        base_pressure: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        Décompose la pression globale en composante buy / sell.
        """
        total_flow = inflow_usd + outflow_usd
        if total_flow <= 0:
            return Decimal(0), Decimal(0)

        inflow_ratio = inflow_usd / total_flow
        outflow_ratio = outflow_usd / total_flow

        buy_pressure = base_pressure * inflow_ratio
        sell_pressure = base_pressure * outflow_ratio

        return buy_pressure, sell_pressure

    # ----------------------------------------------------------------------
    def _smooth(self, previous: Decimal, current: Decimal) -> Decimal:
        """
        Lissage EMA : new = alpha * current + (1 - alpha) * previous
        """
        alpha = Decimal(str(self.config.smoothing_alpha))
        return alpha * current + (Decimal(1) - alpha) * previous

    # ----------------------------------------------------------------------
    def _build_signals(
        self,
        block: int,
        inflow_usd: Decimal,
        outflow_usd: Decimal,
        netflow_usd: Decimal,
        total_usd: float,
        whale_count: int,
        pressure_score: float,
        buy_pressure: float,
        sell_pressure: float,
    ) -> List[WhaleSignal]:
        """
        Génère les signaux interprétables par l'agent.
        """

        signals: List[WhaleSignal] = []

        high_th = self.config.high_pressure_threshold
        mid_th = self.config.medium_pressure_threshold

        # Déterminer direction dominante
        if abs(float(netflow_usd)) < 1.0:
            direction = "neutral"
        elif netflow_usd > 0:
            direction = "buy"
        else:
            direction = "sell"

        # Signal principal suivant la pression
        if pressure_score >= high_th:
            signal_type = "whale_extreme_activity"
        elif pressure_score >= mid_th:
            signal_type = "whale_high_activity"
        else:
            signal_type = "whale_moderate_activity"

        signals.append(
            WhaleSignal(
                chain=self.chain,
                block=block,
                signal_type=signal_type,
                direction=direction,
                pressure_score=pressure_score,
                total_usd=total_usd,
                whale_count=whale_count,
                netflow_usd=float(netflow_usd),
                inflow_usd=float(inflow_usd),
                outflow_usd=float(outflow_usd),
                meta={
                    "buy_pressure": buy_pressure,
                    "sell_pressure": sell_pressure,
                },
            )
        )

        # Signaux d'accumulation / distribution
        if direction == "buy" and buy_pressure >= mid_th:
            signals.append(
                WhaleSignal(
                    chain=self.chain,
                    block=block,
                    signal_type="whale_accumulation",
                    direction="buy",
                    pressure_score=buy_pressure,
                    total_usd=total_usd,
                    whale_count=whale_count,
                    netflow_usd=float(netflow_usd),
                    inflow_usd=float(inflow_usd),
                    outflow_usd=float(outflow_usd),
                    meta={},
                )
            )

        if direction == "sell" and sell_pressure >= mid_th:
            signals.append(
                WhaleSignal(
                    chain=self.chain,
                    block=block,
                    signal_type="whale_distribution",
                    direction="sell",
                    pressure_score=sell_pressure,
                    total_usd=total_usd,
                    whale_count=whale_count,
                    netflow_usd=float(netflow_usd),
                    inflow_usd=float(inflow_usd),
                    outflow_usd=float(outflow_usd),
                    meta={},
                )
            )

        return signals

    # ----------------------------------------------------------------------
    def _push_history(self, snapshot: WhalePressureSnapshot) -> None:
        """
        Stocke l'historique localement (FIFO).
        """
        self._history.append(snapshot)
        if len(self._history) > self.config.max_history:
            self._history.pop(0)

    # ----------------------------------------------------------------------
    def get_history(self) -> List[WhalePressureSnapshot]:
        """
        Récupère l'historique des snapshots.
        Utile pour des features additionnelles (volatilité whales, etc.).
        """
        return list(self._history)
