from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

from bot.bot_core.normalizer.whale_normalizer import WhalePressureSnapshot

logger = logging.getLogger(__name__)


@dataclass
class LiquidityMapConfig:
    """
    Config pour la Liquidity Map par chain.

    - window_blocks : nombre de blocs pris en compte
    - high_liq_threshold_usd : somme USD sur la fenêtre au-dessus de laquelle
      on considère une liquidité très élevée
    - low_liq_threshold_usd : en-dessous = faible liquidité
    - smoothing_alpha : lissage exponentiel du score de liquidité
    """
    enabled: bool = True
    window_blocks: int = 50
    high_liq_threshold_usd: float = 3_000_000.0
    low_liq_threshold_usd: float = 500_000.0
    smoothing_alpha: float = 0.3


@dataclass
class LiquiditySnapshot:
    """
    Photo de la liquidité sur une chain à un bloc donné.
    """
    chain: str
    block: int

    liq_score: float          # 0 → 100
    regime: str               # "low_liquidity" / "medium_liquidity" / "high_liquidity"

    avg_block_usd: float      # moyenne USD par bloc sur la fenêtre
    window_usd: float         # somme USD sur la fenêtre
    whale_density: float      # whales / bloc en moyenne
    window_size: int          # nombre de blocs effectivement pris en compte


class LiquidityMapEngine:
    """
    Liquidity Map PRO (V1)

    S'appuie sur les WhalePressureSnapshot pour estimer un régime de liquidité
    global sur une fenêtre de N blocs.
    """

    def __init__(self, chain: str, config: LiquidityMapConfig) -> None:
        self.chain = chain
        self.config = config

        self._history_usd: List[float] = []
        self._history_whales: List[int] = []
        self._last_liq_score: float = 0.0

    # ------------------------------------------------------------------ #
    # UPDATE
    # ------------------------------------------------------------------ #

    def update_from_snapshot(self, snap: WhalePressureSnapshot) -> LiquiditySnapshot:
        """
        Met à jour la map de liquidité à partir d'un snapshot whales
        et renvoie un LiquiditySnapshot agrégé.
        """
        total_usd = float(snap.total_usd)
        whales = int(snap.whale_count)

        self._history_usd.append(total_usd)
        self._history_whales.append(whales)

        # Keep a sliding window
        max_len = self.config.window_blocks
        if len(self._history_usd) > max_len:
            self._history_usd = self._history_usd[-max_len:]
            self._history_whales = self._history_whales[-max_len:]

        window_usd = sum(self._history_usd)
        n = len(self._history_usd)

        avg_block_usd = window_usd / n if n else 0.0
        total_whales = sum(self._history_whales)
        whale_density = total_whales / n if n else 0.0

        # Score brut basé sur le volume USD sur la fenêtre
        base_score = 0.0
        if window_usd >= self.config.high_liq_threshold_usd:
            base_score = 100.0
        elif window_usd <= self.config.low_liq_threshold_usd:
            base_score = 0.0
        else:
            span = self.config.high_liq_threshold_usd - self.config.low_liq_threshold_usd
            if span > 0:
                base_score = ((window_usd - self.config.low_liq_threshold_usd) / span) * 100.0
            else:
                base_score = 0.0

        # Lissage exponentiel
        alpha = self.config.smoothing_alpha
        liq_score = alpha * base_score + (1.0 - alpha) * self._last_liq_score
        self._last_liq_score = liq_score

        # Régime qualitatif
        regime = "low_liquidity"
        if liq_score >= 66.0:
            regime = "high_liquidity"
        elif liq_score >= 33.0:
            regime = "medium_liquidity"

        snap_liq = LiquiditySnapshot(
            chain=self.chain,
            block=snap.block,
            liq_score=liq_score,
            regime=regime,
            avg_block_usd=avg_block_usd,
            window_usd=window_usd,
            whale_density=whale_density,
            window_size=n,
        )

        logger.info(
            {
                "module": "LiquidityMap",
                "chain": self.chain,
                "block": snap.block,
                "liq_score": liq_score,
                "regime": regime,
                "avg_block_usd": avg_block_usd,
                "window_usd": window_usd,
                "whale_density": whale_density,
                "window_size": n,
            }
        )

        return snap_liq
