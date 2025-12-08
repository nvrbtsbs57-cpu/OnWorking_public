#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
import sys

# ---------------------------------------------------------------------------
# Bootstrap sys.path pour que "bot" soit importable
# ---------------------------------------------------------------------------

CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data" / "godmode"
TRADES_PATH = DATA_DIR / "trades.jsonl"
WALLETS_RUNTIME_PATH = DATA_DIR / "wallets_runtime.json"
EXEC_RUNTIME_PATH = DATA_DIR / "execution_runtime.json"

# Maintenant qu'on a mis le PROJECT_ROOT dans sys.path, on peut importer bot.*
from bot.core.logging import get_logger, setup_logging  # type: ignore

log = get_logger("report_m10_status_v2")


# ---------------------------------------------------------------------------
# Helpers logging
# ---------------------------------------------------------------------------

def setup_default_logging() -> None:
    # On reste cohérent avec le reste du bot : JSON logs, niveau INFO
    setup_logging(level="INFO", json_mode=True)


# ---------------------------------------------------------------------------
# Lecture des trades
# ---------------------------------------------------------------------------

def load_trades(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        log.warning("Fichier trades.jsonl introuvable à %s", path)
        return []

    trades: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                log.warning("Ligne JSONL invalide dans %s: %r", path, line[:120])
    return trades


def summarize_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total_trades": 0,
        "by_symbol": {},
        "by_side": {"buy": 0, "sell": 0},
        "volume_usd_total": "0",
        "realized_pnl_sim_usd": "0",
    }

    vol_total = Decimal("0")
    pnl_total = Decimal("0")

    for t in trades:
        summary["total_trades"] += 1
        symbol = t.get("symbol", "unknown")
        side = t.get("side", "unknown")

        # Compte par symbol
        sym_info = summary["by_symbol"].setdefault(symbol, {"count": 0, "volume_usd": "0"})
        sym_info["count"] += 1

        # Volume notional
        notional_str = t.get("notional", "0")
        try:
            notional = Decimal(str(notional_str))
        except Exception:
            notional = Decimal("0")
        vol_total += notional
        sym_info_vol = Decimal(sym_info["volume_usd"])
        sym_info_vol += notional
        sym_info["volume_usd"] = str(sym_info_vol)

        # Par side
        if side in summary["by_side"]:
            summary["by_side"][side] += 1

        # PnL simulé (si présent, sinon ce sera 0 comme actuellement)
        meta = t.get("meta", {}) or {}
        pnl_sim_str = meta.get("pnl_sim_usd", "0")
        try:
            pnl_sim = Decimal(str(pnl_sim_str))
        except Exception:
            pnl_sim = Decimal("0")
        pnl_total += pnl_sim

    summary["volume_usd_total"] = str(vol_total)
    summary["realized_pnl_sim_usd"] = str(pnl_total)
    return summary


# ---------------------------------------------------------------------------
# Lecture des wallets_runtime.json
# ---------------------------------------------------------------------------

def load_wallets_runtime(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        log.warning("Fichier wallets_runtime.json introuvable à %s", path)
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Impossible de lire wallets_runtime.json: %s", exc)
        return None


def summarize_wallets(runtime_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    On ne fait pas d'hypothèse rigide sur la structure, mais on essaye de lire
    quelques champs classiques : equity_total_usd, wallets, etc.
    """
    res: Dict[str, Any] = {
        "equity_total_usd": runtime_state.get("equity_total_usd"),
        "wallets": {},
    }

    wallets = runtime_state.get("wallets")
    if isinstance(wallets, dict):
        for name, info in wallets.items():
            # info peut être float/usd ou dict ; on log brut si structure inconnue
            res["wallets"][name] = info

    return res


# ---------------------------------------------------------------------------
# Lecture de execution_runtime.json (risk + kill-switch)
# ---------------------------------------------------------------------------

def load_execution_runtime(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        log.warning("Fichier execution_runtime.json introuvable à %s", path)
        return None

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Impossible de lire execution_runtime.json: %s", exc)
        return None


def summarize_execution_runtime(state: Dict[str, Any]) -> Dict[str, Any]:
    kill = state.get("kill_switch", {}) or {}
    return {
        "risk_enabled": bool(state.get("risk_enabled", True)),
        "daily_drawdown_pct": state.get("daily_drawdown_pct"),
        "soft_stop_active": bool(state.get("soft_stop_active", False)),
        "hard_stop_active": bool(state.get("hard_stop_active", False)),
        "kill_switch": {
            "enabled": kill.get("enabled"),
            "tripped": kill.get("tripped"),
            "reason": kill.get("reason"),
        },
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Report M10: résumé des trades (trades.jsonl), des wallets "
            "(wallets_runtime.json) et de l'état risk/kill-switch "
            "(execution_runtime.json)."
        )
    )
    _ = parser.parse_args()

    setup_default_logging()

    log.info("=== M10 – report_m10_status_v2 ===")
    log.info("Dossier data/godmode: %s", DATA_DIR)

    # 1) Trades
    trades = load_trades(TRADES_PATH)
    trades_summary = summarize_trades(trades)
    log.info("Trades summary: %s", json.dumps(trades_summary, sort_keys=True))

    # 2) Wallets runtime
    wallets_state = load_wallets_runtime(WALLETS_RUNTIME_PATH)
    if wallets_state is not None:
        wallets_summary = summarize_wallets(wallets_state)
        log.info("Wallets runtime summary: %s", json.dumps(wallets_summary, sort_keys=True))
    else:
        log.info("Wallets runtime summary: N/A (fichier absent ou illisible).")

    # 3) Execution runtime (risk + kill-switch)
    exec_state = load_execution_runtime(EXEC_RUNTIME_PATH)
    if exec_state is not None:
        exec_summary = summarize_execution_runtime(exec_state)
        log.info("Execution runtime summary: %s", json.dumps(exec_summary, sort_keys=True))
    else:
        log.info("Execution runtime summary: N/A (fichier absent ou illisible).")

    log.info("Report M10 terminé.")


if __name__ == "__main__":
    main()

