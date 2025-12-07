from __future__ import annotations

import asyncio
import json
import pathlib
import time
from typing import Dict, Any, List

from bot.core.logging import get_logger
from .patterns import NormalizedEvent
from .aggregator import EventAggregator

logger = get_logger(__name__)


class NormalizerEngine:
    """
    NormalizerEngine FULL PRO

    - lit les fichiers *.log produits par l'indexer
    - normalise les events -> NormalizedEvent
    - stocke un historique limité dans EventAggregator
    - gestion d'erreurs avec backoff progressif
    - logs structurés pour AlertEngine (via bridge logging)
    """

    def __init__(
        self,
        data_path: str,
        history_size: int = 200,
        poll_interval: float = 1.0,
        max_lines_per_cycle: int = 10_000,
    ) -> None:
        """
        :param data_path: répertoire contenant les fichiers *.log par chain
        :param history_size: taille max de l'historique dans l'aggregator
        :param poll_interval: délai de base entre deux scans (en secondes)
        :param max_lines_per_cycle: limite de lignes lues par tick (sécurité)
        """
        self.data_path = pathlib.Path(data_path)
        self.data_path.mkdir(parents=True, exist_ok=True)

        self.aggregator = EventAggregator(max_size=history_size)
        self.poll_interval = float(poll_interval)
        self.max_lines_per_cycle = int(max_lines_per_cycle)

        self._stop = asyncio.Event()
        self._failure_streak = 0

        logger.info(
            "NormalizerEngine initialisé (path=%s, history_size=%d, poll_interval=%.2fs)",
            self.data_path,
            history_size,
            self.poll_interval,
            extra={
                "event": "normalizer_init",
                "data_path": str(self.data_path),
                "history_size": history_size,
                "poll_interval": self.poll_interval,
            },
        )

    # ------------------------------------------------------------------    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info("NormalizerEngine started", extra={"event": "normalizer_start"})
        await asyncio.gather(self._loop())

    async def stop(self) -> None:
        logger.info("NormalizerEngine stop requested", extra={"event": "normalizer_stop"})
        self._stop.set()

    # ------------------------------------------------------------------    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        last_positions: Dict[str, int] = {}

        while not self._stop.is_set():
            start_ts = time.time()
            total_new_lines = 0
            total_events = 0

            try:
                # Scan des fichiers *.log pour chaque chain
                log_files = list(self.data_path.glob("*.log"))

                if not log_files:
                    logger.debug(
                        "NormalizerEngine: aucun fichier *.log trouvé",
                        extra={"event": "normalizer_no_files"},
                    )

                for chain_file in log_files:
                    chain_name = chain_file.stem
                    pos = last_positions.get(chain_name, 0)

                    try:
                        content = chain_file.read_text(encoding="utf-8")
                    except Exception as e:
                        logger.warning(
                            "NormalizerEngine: impossible de lire %s: %s",
                            chain_file,
                            e,
                            extra={
                                "event": "normalizer_file_read_error",
                                "file": str(chain_file),
                                "error": str(e),
                            },
                        )
                        continue

                    lines = content.splitlines()

                    # Rien de nouveau
                    if pos >= len(lines):
                        continue

                    new_lines = lines[pos : pos + self.max_lines_per_cycle]
                    new_count = len(new_lines)

                    if new_count == 0:
                        continue

                    total_new_lines += new_count

                    for line in new_lines:
                        evt = self._safe_normalize_line(line, chain_name, chain_file)
                        if evt is not None:
                            self.aggregator.add(evt)
                            total_events += 1

                    last_positions[chain_name] = min(
                        len(lines), pos + self.max_lines_per_cycle
                    )

                # tick OK -> reset du failure streak
                self._failure_streak = 0

                elapsed = time.time() - start_ts
                logger.debug(
                    "NormalizerEngine tick: files=%d, new_lines=%d, events=%d, elapsed=%.3fs, agg_size=%d",
                    len(log_files),
                    total_new_lines,
                    total_events,
                    elapsed,
                    len(self.aggregator.get_all()),
                    extra={
                        "event": "normalizer_tick",
                        "files": len(log_files),
                        "new_lines": total_new_lines,
                        "events": total_events,
                        "elapsed": elapsed,
                        "agg_size": len(self.aggregator.get_all()),
                    },
                )

                # Sleep "normal"
                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                # Erreur globale de la boucle -> backoff
                self._failure_streak += 1
                backoff = min(self.poll_interval * (2 ** self._failure_streak), 30.0)

                logger.error(
                    "Error in NormalizerEngine loop (failure_streak=%d, backoff=%.1fs): %s",
                    self._failure_streak,
                    backoff,
                    e,
                    extra={
                        "event": "normalizer_error",
                        "failure_streak": self._failure_streak,
                        "backoff_seconds": backoff,
                        "error": str(e),
                    },
                )

                await asyncio.sleep(backoff)

    # ------------------------------------------------------------------    # Normalisation
    # ------------------------------------------------------------------

    def _safe_normalize_line(
        self, line: str, chain_name: str, chain_file: pathlib.Path
    ) -> Optional[NormalizedEvent]:
        """
        Parse JSON + normalisation, sans faire exploser la boucle.
        """
        if not line.strip():
            return None

        try:
            raw: Dict[str, Any] = json.loads(line)
        except Exception as e:
            logger.debug(
                "NormalizerEngine: ligne JSON invalide dans %s: %s",
                chain_file,
                e,
                extra={
                    "event": "normalizer_bad_json",
                    "file": str(chain_file),
                    "line": line[:200],
                    "error": str(e),
                },
            )
            return None

        try:
            evt = self._normalize(raw)
            return evt
        except Exception as e:
            logger.error(
                "NormalizerEngine: erreur de normalisation pour chain=%s: %s",
                chain_name,
                e,
                extra={
                    "event": "normalizer_normalize_error",
                    "chain": chain_name,
                    "file": str(chain_file),
                    "error": str(e),
                },
            )
            return None

    def _normalize(self, raw: Dict[str, Any]) -> NormalizedEvent:
        """
        MVP amélioré : score basique en fonction du type & notional.
        (Les patterns avancés restent dans patterns/aggregator.)
        """
        chain = raw.get("chain", "unknown")
        block = int(raw.get("block", 0))

        kind = raw.get("type") or raw.get("event_type") or "unknown"
        kind = str(kind)

        # Score de base
        score = 1.0

        # Bonus pour certains types "forts"
        strong_kinds = {"whale_tx", "liquidation", "order_flow_spike"}
        if kind in strong_kinds:
            score += 2.0

        # Si notional_usd présent -> pondération simple
        notional = raw.get("notional_usd") or raw.get("notional")
        try:
            if notional is not None:
                notional_f = float(notional)
                if notional_f > 0:
                    # échelle douce : sqrt, clampée
                    score += min(5.0, (notional_f / 1e5) ** 0.5)
        except Exception:
            pass

        return NormalizedEvent(
            chain=chain,
            block=block,
            kind=kind,
            score=float(score),
            raw=raw,
        )

    # ------------------------------------------------------------------    # Public API
    # ------------------------------------------------------------------

    def get_recent_events(self) -> List[NormalizedEvent]:
        """
        Retourne l'historique courant (limité par history_size).
        """
        return self.aggregator.get_all()
