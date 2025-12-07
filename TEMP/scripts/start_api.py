# scripts/start_api.py
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
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
from bot.api.http_api import run_http_api  # noqa: E402

LOG = logging.getLogger("start_api")


def start_normalizer_background(engine: NormalizerEngine) -> threading.Thread:
    """
    Lance NormalizerEngine.start() dans un thread séparé
    avec son propre event loop asyncio.
    """
    loop = asyncio.new_event_loop()

    def runner() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(engine.start())
        finally:
            loop.close()

    t = threading.Thread(target=runner, name="normalizer_engine", daemon=True)
    t.start()
    return t


def main() -> None:
    cfg_path = Path(BASE_DIR) / "config.json"
    cfg = load_config(cfg_path)

    setup_logging(cfg.logging)
    LOG.info("=== STARTING API (GODMODE) ===")

    # Normalizer qui lit les fichiers de l'indexer
    data_path = cfg.indexer.storage_path
    history_size = cfg.normalizer.history_size

    normalizer = NormalizerEngine(
        data_path=data_path,
        history_size=history_size,
    )

    # on démarre le normalizer en arrière-plan
    _normalizer_thread = start_normalizer_background(normalizer)

    # API HTTP interne
    api_cfg = cfg.api
    server = run_http_api(
        host=api_cfg.host,
        port=api_cfg.port,
        normalizer=normalizer,
    )

    try:
        # On laisse tourner jusqu'à Ctrl+C
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        LOG.info("Stopping API.")
        try:
            # Arrêt propre du serveur HTTP
            server.shutdown()
        except Exception as exc:
            LOG.exception("Erreur lors de l'arrêt du serveur HTTP", exc_info=exc)
        # Le thread du normalizer est daemon, il s'arrêtera avec le process


if __name__ == "__main__":
    main()
