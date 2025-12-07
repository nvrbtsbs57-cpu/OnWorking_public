from __future__ import annotations

import asyncio
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
from bot.bot_core.normalizer.normalizer_engine import NormalizerEngine  # noqa: E402

LOG = logging.getLogger("start_normalizer")


async def main_async() -> None:
    print("=== STARTING NORMALIZER (GODMODE) ===")

    cfg_path = Path(BASE_DIR) / "config.json"
    cfg = load_config(cfg_path)

    # logging -> on passe la sous-config logging
    setup_logging(cfg.logging)

    LOG.info("Starting normalizer engine")

    # le normalizer lit les fichiers NDJSON produits par l'indexer
    data_path = cfg.indexer.storage_path
    history_size = cfg.normalizer.history_size

    engine = NormalizerEngine(
        data_path=data_path,
        history_size=history_size,
    )

    # boucle async principale du normalizer
    await engine.start()


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        LOG.info("Stopping normalizer (KeyboardInterrupt)")


if __name__ == "__main__":
    main()
