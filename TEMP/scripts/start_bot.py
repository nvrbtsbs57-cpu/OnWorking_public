#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

# ----------------------------------------------------------------------
# Racine du projet : BOT_GODMODE/BOT_GODMODE
# ----------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]


def _make_env() -> dict:
    """
    Construit l'environnement pour les sous-processus en forçant PYTHONPATH
    sur ROOT_DIR, comme avant.
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    if existing:
        env["PYTHONPATH"] = f"{ROOT_DIR}{os.pathsep}{existing}"
    else:
        env["PYTHONPATH"] = str(ROOT_DIR)
    return env


def main(argv: Optional[List[str]] = None) -> None:
    """
    Démarre UNIQUEMENT le dashboard FastAPI / GODMODE.

    Le runtime memecoin n'est plus lancé ici :
    - le FULL (dashboard + runtime) est orchestré par scripts/start_godmode_m10.py
      comme défini dans le plan M10.
    """
    env = _make_env()

    print("[START_BOT] Démarrage du dashboard FastAPI (GODMODE).")
    proc = subprocess.Popen(
        [sys.executable, "scripts/start_dashboard.py"],
        cwd=str(ROOT_DIR),
        env=env,
    )

    def _stop():
        if proc.poll() is None:
            print("\n[START_BOT] Arrêt du dashboard...")
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if proc.poll() is None:
                        print(f"[START_BOT] kill forcé du PID {proc.pid}")
                        proc.kill()
            except Exception:
                # On ne doit jamais crasher dans le handler d'arrêt
                pass
            print("[START_BOT] Dashboard arrêté.")

    def _handle_sigint(sig, frame):
        _stop()

    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        # Boucle de garde : on attend que le dashboard se termine
        while True:
            if proc.poll() is not None:
                print("[START_BOT] Le dashboard s'est terminé.")
                break
            time.sleep(1.0)
    finally:
        _stop()


if __name__ == "__main__":
    main()

