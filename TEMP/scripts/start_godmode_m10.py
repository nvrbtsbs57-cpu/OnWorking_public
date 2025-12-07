#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List

# Racine du projet : BOT_GODMODE/BOT_GODMODE
ROOT_DIR = Path(__file__).resolve().parent.parent

# Python courant (idéalement celui du venv)
PYTHON = sys.executable


def spawn_process(cmd: List[str], name: str) -> subprocess.Popen:
    """
    Lance un sous-processus dans ROOT_DIR, log le lancement et renvoie le Popen.
    """
    print(f"[start_all_m10] Lancement {name}: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return proc


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Lance le dashboard GODMODE (FastAPI + UI) et le runtime "
            "memecoin en PAPER_ONCHAIN pour M10."
        )
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Ne pas lancer le dashboard /godmode (start_bot.py).",
    )
    parser.add_argument(
        "--no-runtime",
        action="store_true",
        help="Ne pas lancer le runtime memecoin (test_runtime_memecoin.py).",
    )

    # Paramètres runtime memecoin (avec les valeurs que tu utilisais déjà)
    parser.add_argument("--symbol", default="SOL/USDC", help="Symbol à trader.")
    parser.add_argument("--chain", default="solana", help="Chain (solana, base, bsc, etc.).")
    parser.add_argument("--wallet", default="sniper_sol", help="ID du wallet logique (config.json).")
    parser.add_argument(
        "--engine-notional",
        default="200",
        help="Notional du moteur memecoin (string, passé tel quel au script).",
    )
    parser.add_argument(
        "--exec-min",
        default="2",
        help="Nombre min d'exécutions par cycle (string, passé tel quel).",
    )
    parser.add_argument(
        "--exec-max",
        default="6",
        help="Nombre max d'exécutions par cycle (string, passé tel quel).",
    )
    parser.add_argument(
        "--sleep",
        default="5",
        help="Pause en secondes entre deux cycles d'exécution (string, passé tel quel).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Passe --verbose au runtime memecoin.",
    )

    args = parser.parse_args()

    processes: List[subprocess.Popen] = []

    print(f"[start_all_m10] ROOT_DIR = {ROOT_DIR}")
    print(f"[start_all_m10] PYTHON   = {PYTHON}")
    print()

    try:
        # 1) Dashboard GODMODE (FastAPI + front)
        if not args.no_dashboard:
            processes.append(
                spawn_process(
                    [PYTHON, "scripts/start_bot.py"],
                    name="dashboard",
                )
            )
        else:
            print("[start_all_m10] Dashboard désactivé (--no-dashboard)")

        # 2) Runtime memecoin (PAPER_ONCHAIN, typiquement SOL/USDC sur sniper_sol)
        if not args.no_runtime:
            runtime_cmd: List[str] = [
                PYTHON,
                "scripts/test_runtime_memecoin.py",
                "--symbol",
                args.symbol,
                "--chain",
                args.chain,
                "--wallet",
                args.wallet,
                "--engine-notional",
                str(args.engine_notional),
                "--exec-min",
                str(args.exec_min),
                "--exec-max",
                str(args.exec_max),
                "--sleep",
                str(args.sleep),
            ]
            if args.verbose:
                runtime_cmd.append("--verbose")

            processes.append(
                spawn_process(
                    runtime_cmd,
                    name="memecoin_runtime",
                )
            )
        else:
            print("[start_all_m10] Runtime memecoin désactivé (--no-runtime)")

        print("\n[start_all_m10] Tout est lancé.")
        if not args.no_dashboard:
            print("[start_all_m10] Dashboard : http://127.0.0.1:8001/")
        print("[start_all_m10] Ctrl+C pour arrêter proprement l’ensemble.\n")

        # Boucle d'attente (surveille si un des process meurt)
        while processes:
            alive = [p for p in processes if p.poll() is None]
            if len(alive) != len(processes):
                print("[start_all_m10] Un des processus s'est terminé. Arrêt global.")
                break
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[start_all_m10] Ctrl+C détecté, arrêt des processus...")
    finally:
        # Tentative d'arrêt propre
        for p in processes:
            if p.poll() is None:
                try:
                    p.send_signal(signal.SIGINT)
                except Exception:
                    pass

        # On attend un peu
        time.sleep(2.0)

        # Kill forcé si encore vivant
        for p in processes:
            if p.poll() is None:
                print(f"[start_all_m10] kill -9 pid={p.pid}")
                try:
                    p.kill()
                except Exception:
                    pass

        print("[start_all_m10] Terminé.")


if __name__ == "__main__":
    main()

