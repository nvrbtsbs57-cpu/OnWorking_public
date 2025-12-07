#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_risk_execution_pipeline.py

- charge config.json
- build runtime (M1 + M2 + M3 + RiskAwareExecutionEngine)
- exécute quelques ticks
- affiche un snapshot du TradeStore (positions ouvertes, losing streak)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.runtime import BotRuntime, build_runtime_from_config  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config() -> Dict[str, Any]:
    cfg_path = BASE_DIR / "config.json"
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    setup_logging()
    log = logging.getLogger("test_risk_execution_pipeline")

    raw_cfg = load_config()
    config, deps = build_runtime_from_config(raw_cfg)

    log.info(
        "Runtime construit — bot_name=%s, mode=%s, safety=%s",
        config.bot_name,
        config.execution_mode.value,
        config.safety_mode.value,
    )

    runtime = BotRuntime(config=config, deps=deps)

    # On exécute quelques ticks "à la main" pour voir passer des signaux
    n_ticks = 60
    log.info("Exécution de %d ticks...", n_ticks)

    for i in range(1, n_ticks + 1):
        runtime._tick_once()  # type: ignore[attr-defined]
        time.sleep(config.tick_interval_seconds)

    # Si l'engine d'exécution est le RiskAwareExecutionEngine, on peut inspecter le TradeStore
    execution_engine = deps.execution_engine
    if hasattr(execution_engine, "trade_store"):
        store = execution_engine.trade_store  # type: ignore[assignment]
        log.info("Snapshot TradeStore final: %s", store.debug_snapshot())
    else:
        log.info("Execution engine n'expose pas trade_store, rien à afficher.")

    log.info("Test terminé.")


if __name__ == "__main__":
    main()
