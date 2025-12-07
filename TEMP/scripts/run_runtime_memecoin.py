#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

# -------------------------------------------------------------------
# Bootstrapping : ajouter la racine du repo (BOT_GODMODE) au sys.path
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # .../BOT_GODMODE
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Maintenant on peut importer le package "bot"
from bot.core.runtime import (  # type: ignore
    BotRuntime,
    build_runtime_memecoin_from_config,
)

CONFIG_PATH = BASE_DIR / "config.json"


def load_config() -> Dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    log = logging.getLogger("run_runtime_memecoin")

    log.info("=== Lancement runtime MEMECOIN ===")
    log.info("BASE_DIR   = %s", BASE_DIR)
    log.info("Python exe = %s", sys.executable)

    cfg = load_config()

    # Construction du runtime memecoin (M2–M8)
    config, deps = build_runtime_memecoin_from_config(cfg)

    log.info(
        "Runtime construit — bot_name=%s, exec_mode=%s, safety=%s, tick=%.2fs",
        config.bot_name,
        config.execution_mode.value,
        config.safety_mode.value,
        config.tick_interval_seconds,
    )

    runtime = BotRuntime(config=config, deps=deps)

    try:
        runtime.run_forever()
    except KeyboardInterrupt:
        log.info("Ctrl+C reçu, arrêt du runtime.")
    except Exception:
        log.exception("Erreur non gérée dans le runtime.")
        raise


if __name__ == "__main__":
    main()

