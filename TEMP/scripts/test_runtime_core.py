#!/usr/bin/env python
"""
Petit test end-to-end du runtime core (M1 + M2 + M3 + M4-core).

- charge config.json à la racine du projet,
- ajoute le project root au sys.path pour pouvoir importer le package bot,
- construit le runtime via build_runtime_from_config,
- exécute N ticks manuellement,
- toutes les 10 itérations, si un FinanceEngine concret est présent,
  log un snapshot finance (equity, PnL du jour, fees du jour).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

# ----------------------------------------------------------------------
# Bootstrapping du PYTHONPATH pour trouver le package "bot"
# ----------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[1]  # C:\Users\ME\Documents\BOT_GODMODE

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Maintenant on peut importer le package bot
from bot.core.runtime import BotRuntime, build_runtime_from_config  # type: ignore


LOGGER = logging.getLogger("test_runtime_core")


def load_config() -> dict[str, Any]:
    """
    Charge config.json depuis la racine du repo (dossier parent de scripts/).
    """
    cfg_path = PROJECT_ROOT / "config.json"

    if not cfg_path.is_file():
        raise FileNotFoundError(f"config.json introuvable à {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )

    raw_cfg = load_config()
    config, deps = build_runtime_from_config(raw_cfg)

    LOGGER.info(
        "Démarrage test_runtime_core — bot_name=%s, mode=%s, safety=%s",
        config.bot_name,
        config.execution_mode.value,
        config.safety_mode.value,
    )

    runtime = BotRuntime(config, deps)

    # On utilise start() / _tick_once() / _shutdown() pour garder la même
    # séquence que run_forever(), mais avec un nombre de ticks borné.
    runtime.start()

    max_ticks = 60  # à adapter si tu veux plus long

    try:
        for i in range(1, max_ticks + 1):
            runtime._tick_once()  # type: ignore[attr-defined]

            # Toutes les 10 itérations, on log un snapshot finance si dispo
            if i % 10 == 0 and deps.finance_engine is not None:
                fe: Any = deps.finance_engine
                if hasattr(fe, "build_snapshot"):
                    snap = fe.build_snapshot()
                    LOGGER.info(
                        "Snapshot finance (tick=%d) — equity=%s USD, pnl_today=%s USD, fees_today=%s USD",
                        i,
                        snap.total_equity_usd,
                        snap.total_pnl_today_usd,
                        snap.total_fees_today_usd,
                    )
                else:
                    LOGGER.info(
                        "FinanceEngine n'expose pas build_snapshot() (probablement StubFinanceEngine)."
                    )

            time.sleep(config.tick_interval_seconds)

    except KeyboardInterrupt:
        LOGGER.info("Interruption clavier, arrêt du test.")
    finally:
        runtime._shutdown()  # type: ignore[attr-defined]
        LOGGER.info("test_runtime_core terminé.")


if __name__ == "__main__":
    main()
