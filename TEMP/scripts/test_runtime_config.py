#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de test pour le BotRuntime basé sur config.json.

- Lit config.json (ou un chemin passé avec --config)
- Construit le runtime avec build_runtime_from_config()
- Utilise le vrai RiskEngine + stubs pour le reste
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.runtime import BotRuntime, build_runtime_from_config


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Test BotRuntime avec config.json")
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "config.json"),
        help="Chemin vers le fichier config.json",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config).resolve()
    if not cfg_path.exists():
        print(f"[FATAL] Fichier de config introuvable: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    with cfg_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    setup_logging()
    logger = logging.getLogger("test_runtime_config")
    logger.info("Config chargée depuis %s", cfg_path)

    config, deps = build_runtime_from_config(raw_cfg)
    logger.info(
        "RuntimeConfig: mode=%s, safety=%s, tick=%.2fs, bot_name=%s",
        config.execution_mode.value,
        config.safety_mode.value,
        config.tick_interval_seconds,
        config.bot_name,
    )

    runtime = BotRuntime(config=config, deps=deps)
    logger.info("Démarrage du BotRuntime (config). CTRL+C pour arrêter.")
    try:
        runtime.run_forever()
    except KeyboardInterrupt:
        logger.info("CTRL+C reçu, arrêt du runtime.")
        runtime.stop()


if __name__ == "__main__":
    main()
