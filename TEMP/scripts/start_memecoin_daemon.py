#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Daemon memecoin papier (GODMODE).

Il appelle en boucle le script existant:
  scripts/test_memecoin_paper_trades_sol.py

Ce script de test utilise déjà:
  - WalletFlowsEngine (LIVE_150)
  - ExecutionEngine papier
  - ExecutionRiskAdapter + RiskEngine
  - data/godmode/trades.jsonl

Donc le pipeline est 100% représentatif du futur LIVE, seule la couche d'exécution
reste en PAPER.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

# ----------------------------------------------------------------------
# Bootstrap chemin projet
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

log = logging.getLogger("start_memecoin_daemon")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Daemon memecoin papier (wrap test_memecoin_paper_trades_sol.py)."
    )

    parser.add_argument(
        "--sleep",
        type=float,
        default=60.0,
        help="Pause entre deux cycles memecoin (en secondes).",
    )
    parser.add_argument(
        "--exec-min",
        type=str,
        default="2",
        help="Notional min en USD passé au script memecoin (ex: 2).",
    )
    parser.add_argument(
        "--exec-max",
        type=str,
        default="6",
        help="Notional max en USD passé au script memecoin (ex: 6).",
    )

    return parser.parse_args()


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)7s | %(name)s | %(message)s",
    )

    script_path = BASE_DIR / "scripts" / "test_memecoin_paper_trades_sol.py"
    if not script_path.exists():
        log.error(
            "Script memecoin introuvable: %s (attendu pour le daemon).",
            script_path,
        )
        sys.exit(1)

    log.info("=== MEMECOIN_DAEMON (wrapper test_memecoin_paper_trades_sol.py) ===")
    log.info(
        "Config: sleep=%.1fs, exec_range=%s-%s USD",
        args.sleep,
        args.exec_min,
        args.exec_max,
    )

    try:
        while True:
            cmd = [
                sys.executable,
                str(script_path),
                "--exec-min",
                str(args.exec_min),
                "--exec-max",
                str(args.exec_max),
            ]

            log.info("Lancement cycle memecoin: %s", " ".join(cmd))
            try:
                subprocess.run(
                    cmd,
                    cwd=str(BASE_DIR),
                    check=True,
                )
            except subprocess.CalledProcessError as exc:
                log.exception(
                    "Erreur lors d'un cycle memecoin (le daemon continue): %s", exc
                )

            log.info("Sleep %.1fs avant le prochain cycle memecoin...", args.sleep)
            time.sleep(args.sleep)

    except KeyboardInterrupt:
        log.info("Arrêt manuel du daemon memecoin.")


if __name__ == "__main__":
    main()
