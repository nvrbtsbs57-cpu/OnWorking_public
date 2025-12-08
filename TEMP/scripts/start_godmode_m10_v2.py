#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
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
    print(f"[start_godmode_m10_v2] Lancement {name}: {' '.join(cmd)}")
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
            "memecoin M10 v2 en PAPER_ONCHAIN."
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
        help=(
            "Ne pas lancer le runtime memecoin "
            "(run_m10_memecoin_runtime_v2.py)."
        ),
    )

    # Paramètres runtime memecoin (v2)
    parser.add_argument(
        "--ticks",
        default="0",
        help="Nombre de ticks à exécuter (string, passé tel quel, 0 = infini).",
    )
    parser.add_argument(
        "--sleep",
        default="5",
        help=(
            "Pause en secondes entre deux cycles d'exécution "
            "(string, passé tel quel)."
        ),
    )

    args = parser.parse_args()

    processes: List[subprocess.Popen] = []

    print(f"[start_godmode_m10_v2] ROOT_DIR = {ROOT_DIR}")
    print(f"[start_godmode_m10_v2] PYTHON   = {PYTHON}")
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
            print("[start_godmode_m10_v2] Dashboard désactivé (--no-dashboard)")

        # 2) Runtime memecoin M10 v2 (PAPER_ONCHAIN, config.json)
        if not args.no_runtime:
            runtime_cmd: List[str] = [
                PYTHON,
                "scripts/run_m10_memecoin_runtime_v2.py",
                "--ticks",
                str(args.ticks),
                "--sleep",
                str(args.sleep),
            ]

            processes.append(
                spawn_process(
                    runtime_cmd,
                    name="memecoin_runtime_v2",
                )
            )
        else:
            print("[start_godmode_m10_v2] Runtime memecoin désactivé (--no-runtime)")

        print("\n[start_godmode_m10_v2] Tout est lancé.")
        if not args.no_dashboard:
            print("[start_godmode_m10_v2] Dashboard : http://127.0.0.1:8001/")
            print("[start_godmode_m10_v2] Endpoints GODMODE :")
            print("  - /godmode/wallets/runtime")
            print("  - /godmode/alerts/finance")
            print("  - /godmode/trades/runtime")
        print("[start_godmode_m10_v2] Ctrl+C pour arrêter proprement l’ensemble.\n")

        # Boucle d'attente (surveille si un des process meurt)
        while processes:
            alive = [p for p in processes if p.poll() is None]
            if len(alive) != len(processes):
                print("[start_godmode_m10_v2] Un des processus s'est terminé.")
                print("[start_godmode_m10_v2] Arrêt global.")
                break
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[start_godmode_m10_v2] Ctrl+C détecté, arrêt des processus...")
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
                print(f"[start_godmode_m10_v2] kill -9 pid={p.pid}")
                try:
                    p.kill()
                except Exception:
                    pass

        print("[start_godmode_m10_v2] Terminé.")


if __name__ == "__main__":
    main()

