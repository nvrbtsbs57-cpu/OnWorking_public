#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scripts/run_memecoin_runtime.py

Entrypoint pour le runtime MEMECOIN (memecoin_farming + copy_trading)
en mode GODMODE / PAPER, avec provider onchain_dry_run.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

# -------------------------------------------------------------------
# Bootstrapping du projet (ajout de BOT_GODMODE dans sys.path)
# -------------------------------------------------------------------

# .../BOT_GODMODE/scripts/run_memecoin_runtime.py -> .../BOT_GODMODE
BASE_DIR = Path(__file__).resolve().parent.parent
BOT_DIR = BASE_DIR / "bot"
CONFIG_PATH = BASE_DIR / "config.json"

# On ajoute la racine du repo dans sys.path
if str(BASE_DIR) not in sys.path:
  sys.path.insert(0, str(BASE_DIR))

# Sanity check : le dossier bot doit exister
if not BOT_DIR.exists():
  raise SystemExit(
      f"[run_memecoin_runtime] Répertoire 'bot' introuvable : {BOT_DIR}"
  )

# -------------------------------------------------------------------
# Imports projet (après avoir préparé sys.path)
# -------------------------------------------------------------------
from bot.core.runtime import (  # type: ignore
  BotRuntime,
  build_runtime_memecoin_from_config,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def load_config() -> Dict[str, Any]:
  if not CONFIG_PATH.exists():
    raise FileNotFoundError(f"config.json introuvable à {CONFIG_PATH}")
  with CONFIG_PATH.open("r", encoding="utf-8") as f:
    return json.load(f)


def setup_logging() -> None:
  logging.basicConfig(
      level=logging.INFO,
      format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
  )


# -------------------------------------------------------------------
# Entrypoint runtime memecoin
# -------------------------------------------------------------------
def main() -> None:
  setup_logging()
  log = logging.getLogger("run_memecoin_runtime")

  log.info("=== Lancement runtime MEMECOIN GODMODE (PAPER) ===")
  log.info("BASE_DIR      = %s", BASE_DIR)
  log.info("Python exe    = %s", sys.executable)
  log.info("sys.path[0]   = %s", sys.path[0])

  try:
    cfg = load_config()
  except Exception:
    log.exception("Impossible de charger config.json")
    raise

  # Construction du runtime memecoin (M2–M8)
  config, deps = build_runtime_memecoin_from_config(cfg)

  log.info(
      "Runtime construit — bot_name=%s, exec_mode=%s, safety=%s",
      config.bot_name,
      config.execution_mode.value,
      config.safety_mode.value,
  )

  runtime = BotRuntime(config=config, deps=deps)

  # Boucle principale (Ctrl+C pour arrêter)
  try:
    runtime.run_forever()
  except KeyboardInterrupt:
    log.info("Ctrl+C reçu, arrêt du runtime.")
  except Exception:
    log.exception("Erreur non gérée dans le runtime.")
    raise


if __name__ == "__main__":
  main()

