from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class IndexerStorage:
    """
    Stockage NDJSON basique :
    - 1 dossier par chaîne
    - blocks.ndjson : 1 bloc = 1 ligne JSON
    """

    def __init__(self, base_path: str | Path = "data/indexer") -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info("IndexerStorage initialized at %s", self.base_path)

    def write_block(self, chain: str, block: dict[str, Any]) -> None:
        chain_dir = self.base_path / chain
        chain_dir.mkdir(parents=True, exist_ok=True)

        file_path = chain_dir / "blocks.ndjson"
        with file_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(block, ensure_ascii=False) + "\n")

    def write_meta(self, chain: str, payload: dict[str, Any]) -> None:
        chain_dir = self.base_path / chain
        chain_dir.mkdir(parents=True, exist_ok=True)

        file_path = chain_dir / "meta.json"
        file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
