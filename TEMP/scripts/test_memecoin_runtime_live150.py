#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict
from decimal import Decimal  # au cas où certains modèles l'utilisent
import sys

# --- Préparation du sys.path pour que "bot" soit importable même lancé en script ---

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
RUNTIME_WALLETS_PATH = PROJECT_ROOT / "data" / "godmode" / "wallets_runtime.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- Imports projet ---

from bot.core.logging import get_logger
from bot.wallets.runtime_manager import RuntimeWalletManager
from bot.trading.execution import ExecutionEngine
from bot.trading.paper_trader import PaperTrader, PaperTraderConfig

# Strat memecoin : on utilise la factory prévue dans agent.py
from bot.strategies.memecoin_farming.agent import (
    MemecoinStrategyEngine,
    build_memecoin_strategy_from_config,
)

log = get_logger("test_memecoin_runtime_live150")

CONFIG_PATH = PROJECT_ROOT / "config.json"


# ---------- Helpers ----------


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"[FATAL] config.json introuvable à {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_runtime_wallet_manager(cfg: Dict[str, Any]) -> RuntimeWalletManager:
    """
    Construit le RuntimeWalletManager à partir de config.json.

    Le profil (LIVE_150, etc.) est supposé être géré dans la config elle-même.
    """
    try:
        rwm = RuntimeWalletManager.from_config(cfg)
    except TypeError:
        log.error(
            "RuntimeWalletManager.from_config(...) a une signature différente.\n"
            "Ouvre bot/wallets/runtime_manager.py et adapte build_runtime_wallet_manager()."
        )
        raise
    log.info("RuntimeWalletManager initialisé à partir de config.json.")
    return rwm


def build_execution_engine(runtime_wallet_manager: RuntimeWalletManager) -> ExecutionEngine:
    pt_cfg = PaperTraderConfig.from_env()
    paper_trader = PaperTrader(config=pt_cfg)
    exec_engine = ExecutionEngine(
        inner_engine=paper_trader,
        wallet_manager=runtime_wallet_manager,
    )
    log.info("ExecutionEngine PAPER initialisé avec PaperTrader + RuntimeWalletManager.")
    return exec_engine


def build_memecoin_engine(cfg: Dict[str, Any]) -> MemecoinStrategyEngine:
    """
    Construit le moteur de stratégie memecoin via la factory officielle.

    On délègue à build_memecoin_strategy_from_config() défini dans agent.py.
    """
    engine = build_memecoin_strategy_from_config(cfg, logger_=log)
    log.info("MemecoinStrategyEngine initialisé via build_memecoin_strategy_from_config().")
    return engine


# ---------- Main runtime loop ----------


def main(loop_sleep: float = 5.0) -> None:
    log.info("=== M10 – test_memecoin_runtime_live150 (PAPER_ONCHAIN) ===")
    cfg = load_config()

    runtime_wallet_manager = build_runtime_wallet_manager(cfg)
    exec_engine = build_execution_engine(runtime_wallet_manager)
    memecoin_engine = build_memecoin_engine(cfg)

    log.info("Boucle runtime memecoin démarrée (Ctrl+C pour arrêter).")

    iteration = 0
    try:
        while True:
            iteration += 1

            # 1) Tick wallets (fees, buffers, flows…)
            if hasattr(runtime_wallet_manager, "on_tick"):
                runtime_wallet_manager.on_tick()

            # 2) Générer les signaux memecoin (ENTRY + EXIT) via next_signals()
            if hasattr(memecoin_engine, "next_signals"):
                signals = memecoin_engine.next_signals()
            elif hasattr(memecoin_engine, "generate_signals"):
                # fallback : vieux pattern de tests unitaires
                signals = memecoin_engine.generate_signals()
            else:
                log.error(
                    "memecoin_engine n'a ni next_signals() ni generate_signals(). "
                    "Vérifie bot/strategies/memecoin_farming/agent.py."
                )
                break

            if signals:
                log.info("Itération %s : %s signal(s) memecoin.", iteration, len(signals))
            else:
                log.debug("Itération %s : aucun signal memecoin.", iteration)

            # 3) Envoyer les signaux dans l'ExecutionEngine PAPER
            for sig in signals:
                try:
                    exec_engine.execute_signal(sig)  # PaperTrader → trades.jsonl + PnL simulé
                except Exception as exc:  # pragma: no cover
                    log.exception("Erreur lors de l'exécution du signal %r : %s", sig, exc)

            # 4) Hook on_tick() de la stratégie (no-op pour l'instant, mais future-proof)
            if hasattr(memecoin_engine, "on_tick"):
                try:
                    memecoin_engine.on_tick()
                except Exception as exc:  # pragma: no cover
                    log.warning("Erreur dans memecoin_engine.on_tick(): %s", exc)

            # 5) Snapshot rapide des wallets (LIVE_150) via le fichier runtime
            try:
                if RUNTIME_WALLETS_PATH.exists():
                    with RUNTIME_WALLETS_PATH.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    total_eq = data.get("equity_total_usd")
                    wallets = data.get("wallets", {})
                    sniper = wallets.get("sniper_sol")
                    copy = wallets.get("copy_sol")
                    log.info(
                        "Snapshot LIVE_150 – equity_total=%s | sniper_sol=%s | copy_sol=%s",
                        total_eq,
                        sniper,
                        copy,
                    )
                else:
                    log.warning(
                        "Fichier wallets_runtime.json introuvable à %s",
                        RUNTIME_WALLETS_PATH,
                    )
            except Exception as exc:  # pragma: no cover
                log.warning("Impossible de lire le snapshot wallets_runtime.json : %s", exc)

            time.sleep(loop_sleep)
    except KeyboardInterrupt:
        log.info("Arrêt demandé par l'utilisateur (Ctrl+C).")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Runtime memecoin PAPER_ONCHAIN pour le profil LIVE_150."
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=5.0,
        help="Pause entre deux itérations (secondes, défaut 5).",
    )
    args = parser.parse_args()
    main(loop_sleep=args.sleep)

