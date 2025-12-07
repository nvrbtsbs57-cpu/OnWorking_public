#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de test pour le BotRuntime (stubs).

- Utilise build_runtime_stub() depuis bot.core.runtime
- Tourne en ExecutionMode.PAPER_ONCHAIN
- Permet de valider la boucle principale sans toucher à AgentEngine.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.runtime import (
    BotRuntime,
    ExecutionMode,
    SafetyMode,
    build_runtime_stub,
)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    setup_logging()
    logger = logging.getLogger("test_runtime_stub")

    logger.info("Initialisation du runtime stub (PAPER_ONCHAIN / SAFE).")

    config, deps = build_runtime_stub(
        execution_mode=ExecutionMode.PAPER_ONCHAIN,
        safety_mode=SafetyMode.SAFE,
        tick_interval_seconds=1.0,
    )

    runtime = BotRuntime(config=config, deps=deps)

    logger.info("Démarrage du BotRuntime stub. CTRL+C pour arrêter.")
    try:
        runtime.run_forever()
    except KeyboardInterrupt:
        logger.info("CTRL+C reçu, arrêt du runtime.")
        runtime.stop()


if __name__ == "__main__":
    main()
