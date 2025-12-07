from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, List

logger = logging.getLogger(__name__)


class IndexerStorage:
    """
    Stockage des événements d'indexer.

    - 1 fichier <chain>.log : 1 événement JSON par ligne
    - 1 fichier <chain>.meta.json : meta (dont last_block)

    Ce format est compatible avec :
      - IndexerEngine (get_last_block / save_events / set_last_block)
      - NormalizerEngine qui lit les *.log dans data/indexer
    """

    def __init__(self, base_path: str | Path = "data/indexer") -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info("IndexerStorage initialized at %s", self.base_path)

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _events_path(self, chain: str) -> Path:
        return self.base_path / f"{chain}.log"

    def _meta_path(self, chain: str) -> Path:
        return self.base_path / f"{chain}.meta.json"

    # ------------------------------------------------------------------
    # API utilisée par IndexerEngine
    # ------------------------------------------------------------------

    def get_last_block(self, chain: str) -> int:
        meta_path = self._meta_path(chain)
        if not meta_path.exists():
            return 0

        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return int(data.get("last_block", 0))
        except Exception:
            logger.exception("Failed to read last_block for chain=%s", chain)
            return 0

    def set_last_block(self, chain: str, block: int) -> None:
        meta_path = self._meta_path(chain)
        payload = {"last_block": int(block)}
        try:
            meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write last_block for chain=%s", chain)

    def save_events(self, chain: str, events: List[dict[str, Any]]) -> None:
        """
        Append les events au fichier <chain>.log, 1 JSON par ligne.
        """
        if not events:
            return

        events_path = self._events_path(chain)
        events_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with events_path.open("a", encoding="utf-8") as f:
                for ev in events:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to save events for chain=%s", chain)

    # ------------------------------------------------------------------
    # Compat helper (si du vieux code appelle encore write_block/meta)
    # ------------------------------------------------------------------

    def write_block(self, chain: str, block: dict[str, Any]) -> None:
        """
        Compat : on utilise save_events avec une liste à un élément.
        """
        self.save_events(chain, [block])

    def write_meta(self, chain: str, payload: dict[str, Any]) -> None:
        """
        Compat : on met à jour le meta brut, en gardant last_block si présent.
        """
        meta_path = self._meta_path(chain)
        try:
            meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("Failed to write meta for chain=%s", chain)
