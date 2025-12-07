#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_wallet_runtime_snapshot.py

Test du BotRuntime + RuntimeWalletManager (WalletFlowsEngine) :

- charge config.json (ou un chemin passé avec --config)
- construit le runtime via build_runtime_from_config()
- récupère le wallet_manager (RuntimeWalletManager)
- exécute N ticks "manuels" du runtime
- affiche un snapshot lisible des wallets à chaque tick
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from pprint import pprint

# ---------------------------------------------------------------------
# Mise en place du PYTHONPATH (comme dans test_runtime_config.py)
# ---------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.runtime import BotRuntime, build_runtime_from_config  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test runtime + wallet flows (snapshots de wallets)."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(BASE_DIR / "config.json"),
        help="Chemin vers le fichier de config JSON (par défaut: config.json à la racine du projet)",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=5,
        help="Nombre de ticks à exécuter (par défaut: 5)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging()
    logger = logging.getLogger("test_wallet_runtime_snapshot")

    cfg_path = Path(args.config).resolve()
    if not cfg_path.exists():
        logger.error("Fichier de config introuvable: %s", cfg_path)
        sys.exit(1)

    logger.info("Config chargée depuis %s", cfg_path)
    with cfg_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    # Construction du runtime (M1 + M2 + M3 en cours)
    config, deps = build_runtime_from_config(raw_cfg)

    runtime = BotRuntime(config=config, deps=deps)
    wallet_manager = deps.wallet_manager

    logger.info(
        "Runtime créé — bot_name=%s, mode=%s, safety=%s",
        config.bot_name,
        config.execution_mode.value,
        config.safety_mode.value,
    )

    # Snapshot initial
    logger.info("=== SNAPSHOT INITIAL DES WALLETS ===")
    if hasattr(wallet_manager, "debug_snapshot"):
        snap = wallet_manager.debug_snapshot()
        pprint(snap)
    else:
        logger.warning(
            "wallet_manager ne possède pas debug_snapshot() — type=%s",
            type(wallet_manager),
        )

    # Exécution de quelques ticks manuels
    logger.info("Exécution de %d ticks...", args.ticks)
    for i in range(1, args.ticks + 1):
        # On appelle directement la méthode interne _tick_once()
        # (OK dans un script de test).
        runtime._tick_once()  # type: ignore[attr-defined]
        time.sleep(config.tick_interval_seconds)

        if hasattr(wallet_manager, "debug_snapshot"):
            print(f"\n=== SNAPSHOT APRES TICK {i} ===")
            snap = wallet_manager.debug_snapshot()
            pprint(snap)

    logger.info("Test terminé.")


if __name__ == "__main__":
    main()
