#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Optional, Tuple

# --- Préparation du sys.path pour que "bot" soit importable même lancé en script ---
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- Imports projet ---
from bot.core.logging import get_logger  # type: ignore
from bot.strategies.memecoin_farming import runtime as meme_runtime  # type: ignore
from bot.trading.execution_with_risk import (  # type: ignore
    build_execution_with_risk_from_config,
    get_execution_status_snapshot,
)

log = get_logger("test_m10_memecoin_live_pipeline_v2")


# ---------------------------------------------------------------------------
# Construction du pipeline M10 (LIVE-like mais sans TX)
# ---------------------------------------------------------------------------

def build_m10_pipeline() -> Tuple[Any, Any, Any]:
    """
    Construit le pipeline M10 complet pour la strat memecoin, en réutilisant
    EXACTEMENT les mêmes tuyaux que le runtime live :

    - config.json (+ logging global)
    - RuntimeWalletManager (profil LIVE_150, 10 wallets logiques)
    - ExecutionEngine PAPER (PaperTrader + RuntimeWalletManager)
    - MemecoinStrategyEngine
    - MemecoinRuntime
    - ExecutionWithRisk (RiskEngine + KillSwitch + snapshot writer, DRY_RUN)
    """
    # 1) Config + logging global
    cfg = meme_runtime.load_config()
    meme_runtime.setup_logging_from_config(cfg)
    log.info("Config M10 chargée depuis config.json (RUN_MODE=%r, SAFETY_MODE=%r)",
             cfg.get("RUN_MODE"), cfg.get("SAFETY_MODE"))

    # 2) RuntimeWalletManager partagé entre runtime memecoin ET ExecutionWithRisk
    runtime_wallet_manager = meme_runtime.build_runtime_wallet_manager(cfg, logger_=log)

    # 3) ExecutionEngine PAPER (PaperTrader + RuntimeWalletManager)
    exec_engine = meme_runtime.build_execution_engine(runtime_wallet_manager, logger_=log)

    # 4) Moteur de stratégie memecoin
    memecoin_engine = meme_runtime.build_memecoin_engine(cfg, logger_=log)

    # 5) Config runtime memecoin (symbol, chain, wallet_id, notionals, sleep, ...)
    #    On réutilise le helper interne pour rester aligné sur run_m10_memecoin_runtime.py
    rt_cfg = meme_runtime._build_runtime_config_from_global(cfg)  # type: ignore[attr-defined]

    # 6) Objet runtime "live-like" M10 (PAPER_ONCHAIN)
    runtime = meme_runtime.MemecoinRuntime(
        raw_config=cfg,
        runtime_config=rt_cfg,
        wallet_manager=runtime_wallet_manager,
        execution_engine=exec_engine,
        memecoin_engine=memecoin_engine,
        logger_=log,
    )

    # 7) ExecutionWithRisk (RiskEngine + KillSwitch + ExecutionEngine DRY_RUN + snapshot writer)
    exec_with_risk = build_execution_with_risk_from_config(
        cfg,
        wallet_manager=runtime_wallet_manager,
    )

    log.info(
        "Pipeline M10 construit: MemecoinRuntime + ExecutionWithRisk "
        "(risk_enabled=%s, kill_switch_enabled=%s)",
        getattr(getattr(exec_with_risk, "risk_engine", None), "global_enabled", None),
        getattr(getattr(exec_with_risk, "kill_switch", None), "enabled", None),
    )

    return runtime, runtime_wallet_manager, exec_with_risk


# ---------------------------------------------------------------------------
# Helpers de logs (wallets + risk)
# ---------------------------------------------------------------------------

def log_wallets_snapshot(runtime_wallet_manager: Any) -> None:
    """
    Snapshot in-memory via RuntimeWalletManager.to_runtime_json().
    On ne dépend pas du fichier wallets_runtime.json ici.
    """
    try:
        snap = runtime_wallet_manager.to_runtime_json()
    except Exception as exc:  # pragma: no cover
        log.warning(
            "Impossible de récupérer le snapshot in-memory des wallets: %s",
            exc,
        )
        return

    equity_total = snap.get("equity_total_usd")
    wallets = snap.get("wallets", {}) or {}
    sniper = wallets.get("sniper_sol")
    copy = wallets.get("copy_sol")

    log.info(
        "Snapshot wallets (in-memory) – equity_total=%s | sniper_sol=%s | copy_sol=%s",
        equity_total,
        sniper,
        copy,
    )


def log_execution_snapshot(exec_with_risk: Any) -> None:
    """
    Utilise get_execution_status_snapshot(exec_with_risk) pour récupérer
    l'état courant du RiskEngine + KillSwitch, sans relire le fichier JSON.
    """
    try:
        snap = get_execution_status_snapshot(exec_with_risk)
    except Exception as exc:  # pragma: no cover
        log.warning(
            "Impossible de récupérer le snapshot d'exécution depuis ExecutionWithRisk: %s",
            exc,
        )
        return

    if not isinstance(snap, dict):
        log.warning("Snapshot d'exécution invalide (type=%r)", type(snap))
        return

    kill = snap.get("kill_switch", {}) or {}
    risk_enabled = bool(snap.get("risk_enabled", True))
    daily_dd = snap.get("daily_drawdown_pct")
    soft_stop = bool(snap.get("soft_stop_active", False))
    hard_stop = bool(snap.get("hard_stop_active", False))

    log.info(
        "Snapshot exécution – risk_enabled=%s | kill_switch={enabled=%s, tripped=%s, reason=%r} "
        "| daily_drawdown_pct=%s | soft_stop=%s | hard_stop=%s",
        risk_enabled,
        kill.get("enabled"),
        kill.get("tripped"),
        kill.get("reason"),
        daily_dd,
        soft_stop,
        hard_stop,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(ticks: int = 3, sleep_override: Optional[float] = None) -> None:
    """
    Test "pipeline complet" M10 pour la strat memecoin :

    - construit RuntimeWalletManager + ExecutionEngine + MemecoinRuntime
    - construit ExecutionWithRisk (DRY_RUN) câblé sur le même RuntimeWalletManager
    - exécute quelques ticks de runtime memecoin
    - logge à chaque tick un snapshot wallets + risk/kill-switch

    Ce script N'ENVOIE AUCUNE TX RÉELLE (ExecutionMode.DRY_RUN forcé côté
    ExecutionWithRisk tant que M10 n'est pas finalisé).
    """
    runtime, runtime_wallet_manager, exec_with_risk = build_m10_pipeline()

    # Override éventuel de l'intervalle de tick
    if sleep_override is not None:
        try:
            runtime.config.sleep_seconds = float(sleep_override)
        except Exception:
            log.exception(
                "Impossible d'appliquer sleep_override=%r sur runtime.config.sleep_seconds",
                sleep_override,
            )

    log.info(
        "=== M10 – test_m10_memecoin_live_pipeline_v2 "
        "(PAPER_ONCHAIN, ticks=%d, sleep=%.2fs) ===",
        ticks,
        runtime.config.sleep_seconds,
    )

    # Snapshot initial des wallets
    log_wallets_snapshot(runtime_wallet_manager)

    # Laisser un petit délai pour que le writer de snapshot execution_runtime.json démarre
    time.sleep(1.0)

    for i in range(ticks):
        log.info("Tick memecoin %d/%d – début", i + 1, ticks)
        try:
            executed = runtime.run_once()
        except Exception:
            log.exception("Erreur lors de MemecoinRuntime.run_once()")
            break

        log.info(
            "Tick memecoin %d/%d – %d signaux exécutés.",
            i + 1,
            ticks,
            executed,
        )

        # Snapshot risk / kill switch après exécution du tick
        log_execution_snapshot(exec_with_risk)

        time.sleep(float(runtime.config.sleep_seconds))

    log.info("Test M10 pipeline memecoin terminé.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Test M10 complet: pipeline memecoin (RuntimeWalletManager + ExecutionEngine "
            "+ MemecoinRuntime) + ExecutionWithRisk (PAPER_ONCHAIN, DRY_RUN)."
        )
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=3,
        help="Nombre de ticks à exécuter avant sortie (défaut: 3).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=None,
        help=(
            "Override de l'intervalle entre ticks en secondes "
            "(défaut: valeur de config.json / MemecoinRuntimeConfig)."
        ),
    )
    args = parser.parse_args()
    main(ticks=args.ticks, sleep_override=args.sleep)

