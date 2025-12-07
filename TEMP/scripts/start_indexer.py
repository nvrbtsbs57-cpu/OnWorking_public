from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# ============================================================
# Setup du path pour pouvoir importer "bot.*"
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from bot.config import load_config  # noqa: E402
from bot.logging_config import setup_logging  # noqa: E402
from bot.chains.registry import ChainRegistry  # noqa: E402
from bot.bot_core.indexer.storage import IndexerStorage  # noqa: E402
from bot.bot_core.indexer.indexer_engine import IndexerEngine  # noqa: E402

LOG = logging.getLogger("start_indexer")


async def main() -> None:
    print("=== STARTING INDEXER (GODMODE) ===")

    cfg_path = Path(BASE_DIR) / "config.json"
    cfg = load_config(cfg_path)

    # logging -> sous-config logging
    setup_logging(cfg.logging)

    # on relit le json brut pour récupérer "chains" tel quel
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    chains_raw = raw.get("chains", [])

    if isinstance(chains_raw, dict):
        chains_list = list(chains_raw.values())
    else:
        chains_list = chains_raw

    registry = ChainRegistry()
    registry.register_from_config(chains_list)

    # storage indexer : chemin défini dans cfg.indexer.storage_path
    storage_path = cfg.indexer.storage_path
    storage = IndexerStorage(storage_path)

    # On crée l'engine avec les chains et le storage
    engine = IndexerEngine(
        chains=registry.get_all(),
        storage=storage,
    )

    try:
        # 👉 Dans ta version, la méthode principale s'appelle très probablement "start"
        await engine.start()
    except asyncio.CancelledError:
        pass
    finally:
        # stop() existe déjà (on le voit dans les logs "IndexerEngine stop requested")
        await engine.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted by user")
