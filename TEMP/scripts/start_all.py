#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List

# Racine du projet : BOT_GODMODE/BOT_GODMODE
ROOT_DIR = Path(__file__).resolve().parent.parent

PYTHON = sys.executable  # le python du venv


def spawn_process(cmd: list[str], name: str) -> subprocess.Popen:
    """
    Lance un sous-processus dans ROOT_DIR, log le lancement et renvoie le Popen.
    """
    print(f"[start_all] Lancement {name}: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    return proc


def main() -> None:
    processes: List[subprocess.Popen] = []

    try:
        # 1) Dashboard GODMODE (FastAPI + front)
        processes.append(
            spawn_process(
                [PYTHON, "scripts/start_bot.py"],
                name="dashboard",
            )
        )

        # 2) Runtime memecoin (PAPER_ONCHAIN, SOL/USDC sur sniper_sol)
        processes.append(
            spawn_process(
                [
                    PYTHON,
                    "scripts/test_runtime_memecoin.py",
                    "--symbol",
                    "SOL/USDC",
                    "--chain",
                    "solana",
                    "--wallet",
                    "sniper_sol",
                    "--engine-notional",
                    "200",
                    "--exec-min",
                    "2",
                    "--exec-max",
                    "6",
                    "--sleep",
                    "5",
                    "--verbose",
                ],
                name="memecoin_runtime",
            )
        )

        print("\n[start_all] Tout est lancé.")
        print("[start_all] Dashboard : http://127.0.0.1:8001/")
        print("[start_all] Ctrl+C pour arrêter proprement l’ensemble.\n")

        # Boucle d'attente (surveille si un des process meurt)
        while True:
            alive = [p for p in processes if p.poll() is None]
            if len(alive) != len(processes):
                print("[start_all] Un des processus s'est terminé. Arrêt global.")
                break
            time.sleep(1.0)

    except KeyboardInterrupt:
        print("\n[start_all] Ctrl+C détecté, arrêt des processus...")
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
                print(f"[start_all] kill -9 pid={p.pid}")
                try:
                    p.kill()
                except Exception:
                    pass

        print("[start_all] Terminé.")


if __name__ == "__main__":
    main()

