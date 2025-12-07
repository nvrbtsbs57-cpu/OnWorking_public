from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Deque
from collections import deque

from bot.config import NormalizerConfig
from .models import NormalizedTick

logger = logging.getLogger(__name__)


@dataclass
class NormalizerState:
    ticks: Deque[NormalizedTick] = field(default_factory=lambda: deque(maxlen=2000))
    last_block_ts: float = 0.0


class NormalizerEngine:
    """
    NORMALIZER V3 — GODMODE
    ----------------------------------------
    Prend les blocs indexés par l’indexer,
    et les convertit en ticks normalisés,
    prêts à être utilisés par l’agent.

    - Lit blocks.ndjson pour chaque chaîne active
    - Détecte l’activité et fluctuations simples
    - Maintient un historique de ticks
    - Thread safe
    """

    def __init__(self, cfg: NormalizerConfig) -> None:
        self.cfg = cfg
        self.state = NormalizerState()
        self._stop_event = threading.Event()

        self.indexer_data_path = Path("data/indexer")
        self.last_positions: Dict[str, int] = {}  # offset dans blocks.ndjson

    # ---------------------------------------------------------------------------
    # Thread runner
    # ---------------------------------------------------------------------------

    def start_in_thread(self, name: str = "normalizer") -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name=name, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop_event.set()

    # ---------------------------------------------------------------------------
    # Main loop
    # ---------------------------------------------------------------------------

    def run_forever(self) -> None:
        logger.info("NormalizerEngine V3 started (history_size=%d)", self.cfg.history_size)

        while not self._stop_event.is_set():
            try:
                self._process_all_chains()
            except Exception:
                logger.exception("Error during normalization loop")

            time.sleep(1.0)

    # ---------------------------------------------------------------------------
    # Core — read indexed blocks
    # ---------------------------------------------------------------------------

    def _process_all_chains(self) -> None:
        if not self.indexer_data_path.exists():
            return

        for chain_dir in self.indexer_data_path.iterdir():
            if not chain_dir.is_dir():
                continue

            chain = chain_dir.name
            blocks_file = chain_dir / "blocks.ndjson"
            if not blocks_file.exists():
                continue

            self._process_chain(chain, blocks_file)

    def _process_chain(self, chain: str, path: Path) -> None:
        last_pos = self.last_positions.get(chain, 0)

        try:
            with path.open("r", encoding="utf-8") as f:
                f.seek(last_pos)
                lines = f.readlines()
                new_pos = f.tell()
        except Exception:
            logger.exception("Failed reading NDJSON for chain %s", chain)
            return

        if not lines:
            return

        self.last_positions[chain] = new_pos

        for line in lines:
            try:
                block = json.loads(line)
            except Exception:
                continue

            tick = self._normalize_block(chain, block)
            if tick:
                self.state.ticks.append(tick)
                logger.info(
                    "Normalized block %d on %s — activity=%.3f volume=%.3f impact=%.3f",
                    tick.block_number,
                    chain,
                    tick.activity_level,
                    tick.volume_estimate,
                    tick.price_impact_estimate,
                )

    # ---------------------------------------------------------------------------
    # Normalization logic
    # ---------------------------------------------------------------------------

    def _normalize_block(self, chain: str, block: dict) -> Optional[NormalizedTick]:
        number = block.get("number")
        timestamp = block.get("timestamp", 0)
        tx_count = block.get("transaction_count", 0)

        # Baseline simple
        activity = tx_count / 200.0  # approx normalization
        volume = tx_count * 0.001    # estimation simplifiée
        impact = 0.0

        if self.state.last_block_ts != 0:
            dt = timestamp - self.state.last_block_ts
            if dt > 0 and tx_count > 0:
                impact = tx_count / dt

        self.state.last_block_ts = timestamp

        return NormalizedTick(
            chain=chain,
            block_number=number,
            timestamp=timestamp,
            tx_count=tx_count,
            activity_level=activity,
            volume_estimate=volume,
            price_impact_estimate=impact
        )
