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

log = get_logger("run_m10_memecoin_runtime_v2")


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
    log.info(
        "Config M10 chargée depuis config.json (RUN_MODE=%r, SAFETY_MODE=%r)",
        cfg.get("RUN_MODE"),
        cfg.get("SAFETY_MODE"),
    )

    # 2) RuntimeWalletManager partagé entre runtime memecoin ET ExecutionWithRisk
    runtime_wallet_manager = meme_runtime.build_runtime_wallet_manager(cfg, logger_=log)

    # 3) ExecutionEngine PAPER (PaperTrader + RuntimeWalletManager)
    exec_engine = meme_runtime.build_execution_engine(runtime_wallet_manager, logger_=log)

    # 4) Moteur de stratégie memecoin
    memecoin_engine = meme_runtime.build_memecoin_engine(cfg, logger_=log)

    # 5) Config runtime memecoin (symbol, chain, wallet_id, notionals, sleep, ...)
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


def log_execution_snapshot(exec_with_risk: Any, tick: int) -> bool:
    """
    Utilise get_execution_status_snapshot(exec_with_risk) pour récupérer
    l'état courant du RiskEngine + KillSwitch, sans relire le fichier JSON.

    Retourne:
        True  si kill_switch.tripped == True
        False sinon
    """
    try:
        snap = get_execution_status_snapshot(exec_with_risk)
    except Exception as exc:  # pragma: no cover
        log.warning(
            "[tick=%d] Impossible de récupérer le snapshot d'exécution: %s",
            tick,
            exc,
        )
        return False

    if not isinstance(snap, dict):
        log.warning("[tick=%d] Snapshot d'exécution invalide (type=%r)", tick, type(snap))
        return False

    kill = snap.get("kill_switch", {}) or {}
    risk_enabled = bool(snap.get("risk_enabled", True))
    daily_dd = snap.get("daily_drawdown_pct")
    soft_stop = bool(snap.get("soft_stop_active", False))
    hard_stop = bool(snap.get("hard_stop_active", False))
    kill_tripped = bool(kill.get("tripped"))

    log.info(
        "[tick=%d] Execution snapshot – risk_enabled=%s | kill_switch={enabled=%s, "
        "tripped=%s, reason=%r} | daily_drawdown_pct=%s | soft_stop=%s | hard_stop=%s",
        tick,
        risk_enabled,
        kill.get("enabled"),
        kill_tripped,
        kill.get("reason"),
        daily_dd,
        soft_stop,
        hard_stop,
    )

    return kill_tripped


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop(ticks: int, sleep_override: Optional[float]) -> None:
    """
    Boucle principale M10 :

    - construit RuntimeWalletManager + ExecutionEngine + MemecoinRuntime
    - construit ExecutionWithRisk (DRY_RUN) câblé sur le même RuntimeWalletManager
    - exécute des ticks en continu (ticks>0 -> limité, ticks=0 -> infini)
    - écrit:
        - trades.jsonl (PaperTrader)
        - wallets_runtime.json (RuntimeWalletManager)
        - execution_runtime.json (ExecutionWithRisk snapshot writer)
    - S'ARRÊTE dès que le kill-switch est TRIPPED.
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

    sleep_s = float(runtime.config.sleep_seconds)
    log.info(
        "=== M10 – run_m10_memecoin_runtime_v2 (PAPER_ONCHAIN, ticks=%s, sleep=%.2fs) ===",
        "∞" if ticks == 0 else ticks,
        sleep_s,
    )

    tick_index = 0
    try:
        while True:
            tick_index += 1
            if ticks > 0 and tick_index > ticks:
                break

            log.info("Tick memecoin %d/%s – début", tick_index, "∞" if ticks == 0 else ticks)
            try:
                executed = runtime.run_once()
            except Exception:
                log.exception("Erreur lors de MemecoinRuntime.run_once() (tick=%d)", tick_index)
                break

            log.info(
                "Tick memecoin #%d — %d signaux exécutés.",
                tick_index,
                executed,
            )

            # Snapshot risk / kill switch après exécution du tick
            kill_tripped = log_execution_snapshot(exec_with_risk, tick_index)
            if kill_tripped:
                log.warning(
                    "Kill-switch TRIPPED détecté au tick=%d – arrêt de la boucle M10.",
                    tick_index,
                )
                break

            time.sleep(sleep_s)
    except KeyboardInterrupt:
        log.info("Interruption clavier reçue, arrêt propre du runtime M10...")
    finally:
        log.info("run_m10_memecoin_runtime_v2 terminé.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Runner M10 complet: pipeline memecoin (RuntimeWalletManager + ExecutionEngine "
            "+ MemecoinRuntime) + ExecutionWithRisk (PAPER_ONCHAIN, DRY_RUN)."
        )
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=0,
        help=(
            "Nombre de ticks à exécuter avant sortie. "
            "0 = boucle infinie (défaut: 0)."
        ),
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
    run_loop(ticks=args.ticks, sleep_override=args.sleep)


if __name__ == "__main__":
    main()

