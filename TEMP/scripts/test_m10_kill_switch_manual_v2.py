#!/usr/bin/env python3
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict
import sys

# --- Préparation du sys.path pour que "bot" soit importable même lancé en script ---
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONFIG_PATH = PROJECT_ROOT / "config.json"
EXEC_RUNTIME_PATH = PROJECT_ROOT / "data" / "godmode" / "execution_runtime.json"

# --- Imports projet ---
from bot.core.logging import get_logger, setup_logging
from bot.wallets.runtime_manager import RuntimeWalletManager
from bot.trading.execution_with_risk import (
    build_execution_with_risk_from_config,
    get_execution_status_snapshot,
)

log = get_logger("test_m10_kill_switch_manual_v2")


# ---------- Helpers génériques ----------

def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"[FATAL] config.json introuvable à {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging_from_config(raw_cfg: Dict[str, Any]) -> None:
    """
    Initialise le logging global à partir de config.json["logging"].

    Exemple attendu dans config.json :
    "logging": {
      "level": "INFO",
      "json": true
    }
    """
    level = "INFO"
    json_mode = True
    try:
        log_cfg = (raw_cfg.get("logging") or {}) if isinstance(raw_cfg, dict) else {}
        level = str(log_cfg.get("level", level))
        json_mode = bool(log_cfg.get("json", json_mode))
    except Exception:
        # En cas de soucis de parsing, on garde les valeurs par défaut
        pass

    setup_logging(level=level, json_mode=json_mode)


# ---------- Construction des composants M10 ----------

def build_runtime_wallet_manager(cfg: Dict[str, Any]) -> RuntimeWalletManager:
    """
    Construit le RuntimeWalletManager à partir de config.json.
    Profil LIVE_150, etc. géré dans la config (wallets.runtime_profile_id, ...).
    """
    try:
        rwm = RuntimeWalletManager.from_config(cfg)
    except TypeError:
        log.error(
            "RuntimeWalletManager.from_config(...) a une signature différente.\n"
            "Ouvre bot/wallets/runtime_manager.py et adapte build_runtime_wallet_manager()."
        )
        raise

    log.info(
        "RuntimeWalletManager initialisé à partir de config.json "
        "(profile_id=%s, equity_total_usd=%s)",
        getattr(rwm, "profile_id", "n/a"),
        getattr(rwm, "equity_total_usd", "n/a"),
    )
    return rwm


def build_execution_with_risk(
    cfg: Dict[str, Any],
    runtime_wallet_manager: RuntimeWalletManager,
):
    """
    Construit ExecutionWithRisk (ExecutionRiskAdapter) en mode PAPER,
    câblé sur RuntimeWalletManager pour les métriques de risk.

    IMPORTANT :
    - Même tuyaux/règles que le live,
    - mais ExecutionEngine reste en DRY_RUN (aucune TX réelle).
    """
    exec_with_risk = build_execution_with_risk_from_config(
        cfg,
        wallet_manager=runtime_wallet_manager,
    )

    log.info(
        "ExecutionWithRisk construit: %s (kill_switch_enabled=%s, risk_enabled=%s)",
        type(exec_with_risk).__name__,
        getattr(getattr(exec_with_risk, "kill_switch", None), "enabled", None),
        getattr(getattr(exec_with_risk, "risk_engine", None), "global_enabled", None),
    )
    return exec_with_risk


# ---------- Logs / snapshots ----------

def log_execution_snapshot_from_file() -> None:
    """
    Lit data/godmode/execution_runtime.json (écrit par ExecutionRuntimeSnapshotWriter)
    et logge les infos importantes : kill_switch, drawdown, etc.
    """
    if not EXEC_RUNTIME_PATH.exists():
        log.warning(
            "Fichier execution_runtime.json introuvable à %s (pas encore créé ?)",
            EXEC_RUNTIME_PATH,
        )
        return

    try:
        with EXEC_RUNTIME_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log.warning(
            "Impossible de lire le snapshot execution_runtime.json : %s", exc
        )
        return

    kill = data.get("kill_switch", {})
    risk_enabled = bool(data.get("risk_enabled", True))
    daily_dd = data.get("daily_drawdown_pct")
    soft_stop = bool(data.get("soft_stop_active", False))
    hard_stop = bool(data.get("hard_stop_active", False))

    log.info(
        "Snapshot exécution (FILE) – risk_enabled=%s | "
        "kill_switch={enabled=%s, tripped=%s, reason=%r} "
        "| daily_drawdown_pct=%s | soft_stop=%s | hard_stop=%s",
        risk_enabled,
        kill.get("enabled"),
        kill.get("tripped"),
        kill.get("reason"),
        daily_dd,
        soft_stop,
        hard_stop,
    )


def log_execution_snapshot_in_memory(exec_with_risk: Any) -> None:
    """
    Snapshot in-memory via get_execution_status_snapshot(exe=...).
    Ne dépend pas du fichier JSON, lit directement l'état de l'ExecutionWithRisk.
    """
    try:
        snap = get_execution_status_snapshot(exec_with_risk)
    except Exception as exc:
        log.warning(
            "Impossible de récupérer le snapshot in-memory ExecutionWithRisk: %s",
            exc,
        )
        return

    kill = snap.get("kill_switch", {})
    risk_enabled = bool(snap.get("risk_enabled", True))
    daily_dd = snap.get("daily_drawdown_pct")
    soft_stop = bool(snap.get("soft_stop_active", False))
    hard_stop = bool(snap.get("hard_stop_active", False))

    log.info(
        "Snapshot exécution (MEM) – risk_enabled=%s | "
        "kill_switch={enabled=%s, tripped=%s, reason=%r} "
        "| daily_drawdown_pct=%s | soft_stop=%s | hard_stop=%s",
        risk_enabled,
        kill.get("enabled"),
        kill.get("tripped"),
        kill.get("reason"),
        daily_dd,
        soft_stop,
        hard_stop,
    )


# ---------- Main ----------

def main(wait_seconds: float = 2.0) -> None:
    cfg = load_config()
    setup_logging_from_config(cfg)

    log.info("=== M10 – test_m10_kill_switch_manual_v2 (PAPER_ONCHAIN) ===")

    # 1) RuntimeWalletManager (LIVE_150, 10 wallets logiques, buffers, etc.)
    runtime_wallet_manager = build_runtime_wallet_manager(cfg)

    # 2) ExecutionWithRisk (ExecutionRiskAdapter + RiskEngine + KillSwitch + snapshot writer)
    exec_with_risk = build_execution_with_risk(cfg, runtime_wallet_manager)

    # 3) Snapshot initial (in-memory + fichier, si déjà écrit)
    log.info("Snapshot initial (avant trip du kill-switch) :")
    log_execution_snapshot_in_memory(exec_with_risk)
    log_execution_snapshot_from_file()

    # 4) Trip du KillSwitchState en mémoire (simule un EJECT global via risk)
    ks = getattr(exec_with_risk, "kill_switch", None)
    if ks is None:
        log.error("ExecutionWithRisk.kill_switch est None – rien à tester.")
        return

    reason = "Manual trip from test_m10_kill_switch_manual_v2"
    log.info("Trip du kill-switch en mémoire avec reason=%r ...", reason)
    try:
        ks.trip(reason=reason, from_risk=True)
    except Exception as exc:
        log.error("Erreur lors du trip du kill-switch: %s", exc)
        return

    # 5) Attendre que le writer de snapshot mette à jour execution_runtime.json
    log.info(
        "Attente de %.1fs pour laisser ExecutionRuntimeSnapshotWriter "
        "écrire execution_runtime.json...",
        wait_seconds,
    )
    time.sleep(wait_seconds)

    # 6) Snapshot après trip
    log.info("Snapshot après trip du kill-switch :")
    log_execution_snapshot_in_memory(exec_with_risk)
    log_execution_snapshot_from_file()

    log.info("Test M10 KillSwitch (manual trip) terminé.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Test M10: KillSwitchState (PAPER_ONCHAIN) câblé sur RuntimeWalletManager "
            "LIVE_150 et ExecutionWithRisk. Trip manuel du kill-switch, puis lecture "
            "du snapshot execution_runtime.json."
        )
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=2.0,
        help=(
            "Temps d'attente avant lecture du snapshot execution_runtime.json "
            "(défaut: 2.0s)."
        ),
    )
    args = parser.parse_args()
    main(wait_seconds=args.wait)

