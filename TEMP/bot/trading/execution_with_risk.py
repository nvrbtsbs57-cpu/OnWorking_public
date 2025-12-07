# file: bot/trading/execution_with_risk.py
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from bot.core.logging import get_logger
from bot.core.risk import RiskConfig, RiskEngine
from bot.core.rpc_clients import build_rpc_clients_from_config
from bot.execution.engine import ExecutionEngine, ExecutionMode
from bot.trading.execution_risk_adapter import (
    ExecutionRiskAdapter,
    RuntimeWalletStats,
    KillSwitchState,
)

logger = get_logger(__name__)

_EXECUTION_RUNTIME_PATH = Path("data/godmode/execution_runtime.json")

# Alias pour compat
ExecutionWithRisk = ExecutionRiskAdapter


# ======================================================================
# Kill-switch
# ======================================================================


def _build_kill_switch_from_config(raw_cfg: Dict[str, Any]) -> KillSwitchState:
    exec_cfg = raw_cfg.get("execution", {}) or {}
    ks_raw = exec_cfg.get("kill_switch") or raw_cfg.get("kill_switch") or {}

    kill_switch = KillSwitchState(
        enabled=bool(ks_raw.get("enabled", True)),
        trip_on_risk_eject=bool(ks_raw.get("trip_on_risk_eject", True)),
        manual_tripped=bool(ks_raw.get("manual_tripped", False)),
    )

    logger.info(
        "KillSwitchState initialisé (enabled=%s, trip_on_risk_eject=%s, manual_tripped=%s)",
        kill_switch.enabled,
        kill_switch.trip_on_risk_eject,
        kill_switch.manual_tripped,
    )
    return kill_switch


# ======================================================================
# Construction Execution + Risk
# ======================================================================


def build_execution_with_risk_from_config(
    raw_cfg: Dict[str, Any],
    *,
    wallet_manager: Any,
    paper_trader: Optional[Any] = None,
    paper_engine: Optional[Any] = None,
    position_manager: Optional[Any] = None,
    base_dir: Optional[Path] = None,
    run_mode: Optional[str] = None,
) -> ExecutionRiskAdapter:
    """
    Construit l'adapter ExecutionWithRisk pour GODMODE.

    M10 : même si RUN_MODE=LIVE, on force l'ExecutionEngine en DRY_RUN.
    => tout est "live" côté prix / RPC / risk / wallets,
       mais aucune TX n'est envoyée.
    """

    # 1) Risk engine
    risk_raw = raw_cfg.get("risk", {}) or {}
    safety_mode = str(raw_cfg.get("SAFETY_MODE", "NORMAL")).upper()

    risk_cfg = RiskConfig.from_dict(risk_raw).adjusted_for_safety(safety_mode)
    risk_engine = RiskEngine(config=risk_cfg)

    try:
        risk_engine.set_wallet_metrics(wallet_manager)
    except Exception as exc:
        logger.exception(
            "build_execution_with_risk_from_config: set_wallet_metrics a échoué",
            extra={"error": str(exc)},
        )

    # 2) RPC clients
    run_mode_effective = str(run_mode or raw_cfg.get("RUN_MODE", "paper")).upper()
    rpc_clients = None

    try:
        rpc_clients = build_rpc_clients_from_config(
            raw_cfg,
            run_mode=run_mode_effective,
        )
        logger.info(
            "rpc_clients construits (chains=%s, run_mode=%s)",
            list(rpc_clients.keys()),
            run_mode_effective,
        )
    except Exception as exc:
        logger.exception(
            "Impossible de construire rpc_clients, on continue sans RPC.",
            extra={"error": str(exc)},
        )
        rpc_clients = None

    # 3) ExecutionEngine — DRY_RUN forcé
    engine_mode = ExecutionMode.DRY_RUN
    if run_mode_effective == "LIVE":
        logger.warning(
            "ExecutionMode.LIVE demandé mais verrouillé tant que M10 n'est pas finalisé. "
            "Engine initialisé en DRY_RUN."
        )

    if wallet_manager is None:
        logger.warning(
            "wallet_manager=None — ExecutionEngine créé sans RuntimeWalletManager, "
            "execute() ne doit pas être utilisé dans ce mode."
        )

    base_exec = ExecutionEngine(
        rpc_clients=rpc_clients or {},
        wallet_manager=wallet_manager,
        mode=engine_mode,
    )

    # 4) Kill switch + stats
    kill_switch = _build_kill_switch_from_config(raw_cfg)
    stats_provider = RuntimeWalletStats(wallet_manager=wallet_manager)

    risk_enabled = bool(
        getattr(risk_cfg, "global_cfg", None) and risk_cfg.global_cfg.enabled
    )

    adapter = ExecutionRiskAdapter(
        inner_engine=base_exec,
        risk_engine=risk_engine if risk_enabled else None,
        stats_provider=stats_provider,
        enabled=risk_enabled,
        kill_switch=kill_switch,
    )

    logger.info(
        "ExecutionWithRisk construit (SAFETY_MODE=%s, risk_enabled=%s, kill_switch_enabled=%s)",
        safety_mode,
        adapter.enabled,
        adapter.kill_switch.enabled if adapter.kill_switch else False,
    )

    # 5) Writer de snapshot pour le dashboard
    interval = float(raw_cfg.get("RUNTIME_TICK_INTERVAL_SECONDS", 1.0))
    try:
        _start_execution_snapshot_writer(adapter, interval_seconds=interval)
    except Exception as exc:
        logger.exception(
            "Impossible de démarrer le writer de snapshot execution_runtime.json",
            extra={"error": str(exc)},
        )

    return adapter


# ======================================================================
# Snapshot pour le dashboard
# ======================================================================


def get_execution_status_snapshot(
    exe: Optional[ExecutionRiskAdapter] = None,
) -> Dict[str, Any]:
    """
    Si `exe` est fourni, on lit directement son état courant.
    Sinon, on recharge le snapshot JSON écrit en tâche de fond.
    """

    if exe is None:
        try:
            if not _EXECUTION_RUNTIME_PATH.exists():
                logger.warning(
                    "get_execution_status_snapshot() sans 'exe': %s introuvable",
                    _EXECUTION_RUNTIME_PATH,
                )
                return {}
            raw = _EXECUTION_RUNTIME_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            logger.exception(
                "get_execution_status_snapshot() sans 'exe': erreur lecture %s",
                _EXECUTION_RUNTIME_PATH,
                extra={"error": str(exc)},
            )
            return {}

    # --- via l’instance ExecutionRiskAdapter ---

    risk_enabled = bool(getattr(exe, "enabled", False))

    ks = getattr(exe, "kill_switch", None)
    if ks is not None:
        kill_enabled = bool(getattr(ks, "enabled", True))
        manual_tripped = bool(getattr(ks, "manual_tripped", False))
        risk_tripped = bool(getattr(ks, "risk_tripped", False))
        last_reason = getattr(ks, "last_trip_reason", None)
    else:
        kill_enabled = False
        manual_tripped = False
        risk_tripped = False
        last_reason = None

    risk_engine = getattr(exe, "risk_engine", None)
    if risk_engine is not None:
        daily_dd_pct = getattr(risk_engine, "daily_drawdown_pct", None)
        soft_stop = bool(getattr(risk_engine, "soft_stop_active", False))
        hard_stop = bool(getattr(risk_engine, "hard_stop_active", False))
    else:
        daily_dd_pct = None
        soft_stop = False
        hard_stop = False

    return {
        "risk_enabled": risk_enabled,
        "kill_switch": {
            "enabled": kill_enabled,
            "tripped": bool(manual_tripped or risk_tripped),
            "reason": last_reason,
        },
        "daily_drawdown_pct": daily_dd_pct,
        "soft_stop_active": soft_stop,
        "hard_stop_active": hard_stop,
    }


# ======================================================================
# Writer du snapshot d'exécution
# ======================================================================


def _start_execution_snapshot_writer(
    exe: ExecutionRiskAdapter,
    *,
    interval_seconds: float = 1.0,
    path: Path = _EXECUTION_RUNTIME_PATH,
) -> None:
    """
    Thread de fond qui écrit régulièrement un snapshot minimal de l'état
    exécution + risk dans data/godmode/execution_runtime.json.
    """

    def _loop() -> None:
        while True:
            try:
                snapshot = get_execution_status_snapshot(exe)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    encoding="utf-8",
                )
            except Exception as exc:
                logger.exception(
                    "Erreur lors de l’écriture de %s",
                    path,
                    extra={"error": str(exc)},
                )
            time.sleep(interval_seconds)

    t = threading.Thread(
        target=_loop,
        name="ExecutionSnapshotWriter",
        daemon=True,
    )
    t.start()


__all__ = [
    "ExecutionWithRisk",
    "build_execution_with_risk_from_config",
    "get_execution_status_snapshot",
]

