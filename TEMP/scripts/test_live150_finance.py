#!/usr/bin/env python
# scripts/test_live150_finance.py
#
# Script de test/offline pour le profil LIVE_150.
#
# - Construit un RuntimeWalletManager depuis config.json
# - Rejoue différents scénarios de PnL (gains/pertes, séries de losers)
# - Persiste un snapshot dans data/godmode/wallets_runtime.json
#   pour inspection via /godmode/wallets/runtime et /godmode/status.

from __future__ import annotations

import argparse
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any

# ---------------------------------------------------------------------------
# Bootstrap du PYTHONPATH pour que "import bot" fonctionne
# ---------------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]  # ../ (racine du repo BOT_GODMODE)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bot.wallets.runtime_manager import RuntimeWalletManager, RUNTIME_WALLETS_PATH  # type: ignore


CONFIG_PATH = REPO_ROOT / "config.json"


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------

def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"config.json introuvable à {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_manager(label: str) -> RuntimeWalletManager:
    """
    Construit un RuntimeWalletManager à partir de config.json
    pour un scénario de test donné.
    """
    raw_cfg = load_config()
    logger = logging.getLogger(f"test_live150.{label}")
    logger.setLevel(logging.INFO)
    return RuntimeWalletManager.from_config(raw_cfg, logger=logger)


def print_snapshot(manager: RuntimeWalletManager, title: str) -> None:
    """
    Affiche un snapshot lisible en CLI à partir du RuntimeWalletManager.

    Le RuntimeWalletManager.debug_snapshot() renvoie un dict de la forme :
    {
        "updated_at": "...",
        "wallets_source": "runtime_manager",
        "wallets": {
            "sniper_sol": {
                "balance_usd": "...",
                "realized_pnl_today_usd": "...",
                "gross_pnl_today_usd": "...",
                "fees_paid_today_usd": "...",
                "consecutive_losing_trades": 0,
                "last_reset_date": "..."
            },
            ...
        },
        "wallets_count": 10,
        "equity_total_usd": 150.0,
        "pnl_today_total_usd": ...,
        "pnl_day": {...},
        "profile_id": "LIVE_150"
    }
    """
    snap = manager.debug_snapshot()
    total_equity = manager.get_total_equity_usd()
    equity_from_snapshot = snap.get("equity_total_usd")

    print(f"\n=== {title} ===")
    print(f"Total equity (manager.get_total_equity_usd) : {total_equity}")
    if equity_from_snapshot is not None:
        print(f"equity_total_usd (snapshot)               : {equity_from_snapshot}")

    wallets = snap.get("wallets") or {}
    if not isinstance(wallets, dict):
        print("  [WARN] snapshot['wallets'] n'est pas un dict, snapshot brut :")
        print(snap)
        print(f"(snapshot écrit dans : {RUNTIME_WALLETS_PATH})")
        return

    print("Wallets:")
    for wid in sorted(wallets.keys()):
        w = wallets[wid]

        # Compat legacy : si jamais w est un float (ancien format)
        if not isinstance(w, dict):
            bal = w
            pnl_today = None
            gross_pnl = None
            fees = None
            losers = None
        else:
            bal = w.get("balance_usd")
            pnl_today = w.get("realized_pnl_today_usd")
            gross_pnl = w.get("gross_pnl_today_usd")
            fees = w.get("fees_paid_today_usd")
            losers = w.get("consecutive_losing_trades")

        print(
            f"- {wid:<12} "
            f"balance={bal}  pnl_today={pnl_today}  gross_pnl={gross_pnl}  "
            f"fees_today={fees}  losers={losers}"
        )

    print(f"(snapshot écrit dans : {RUNTIME_WALLETS_PATH})")


# ---------------------------------------------------------------------------
# Scénarios
# ---------------------------------------------------------------------------

def scenario_0_initial() -> None:
    """
    Scénario 0 : sanity check.
    - Initialise le runtime avec LIVE_150
    - Écrit un snapshot initial (150 USD, live_allowed attendu = true)
    """
    manager = build_manager("scenario0_initial")
    # __init__ du manager fait déjà un snapshot de départ
    print_snapshot(manager, "Scénario 0 — État initial LIVE_150")


def scenario_1_small_green_day() -> None:
    """
    Scénario 1 : petit jour vert.
    - sniper_sol : +10 USD
    - copy_sol   : +10 USD
    Objectif : voir auto-fees + profit_splits bouger fees / profits_* / vault.
    """
    manager = build_manager("scenario1_green")

    # Gains sur sniper_sol et copy_sol
    manager.on_trade_closed("sniper_sol", Decimal("10"))
    manager.on_trade_closed("copy_sol", Decimal("10"))

    # Tick de fin de scénario (compounding stub + snapshot)
    manager.on_tick()

    print_snapshot(manager, "Scénario 1 — Petit jour vert (sniper_sol + copy_sol +10$)")


def scenario_2_drawdown_warning() -> None:
    """
    Scénario 2 : drawdown global en zone WARNING.
    - Pertes totales ≈ -20 USD (par ex. -10 sur sniper_sol et -10 sur copy_sol)
    Objectif : equity ~130 USD, alert WARNING GLOBAL_DRAWDOWN_WARNING attendue.
    """
    manager = build_manager("scenario2_dd_warning")

    manager.on_trade_closed("sniper_sol", Decimal("-10"))
    manager.on_trade_closed("copy_sol", Decimal("-10"))

    manager.on_tick()

    print_snapshot(manager, "Scénario 2 — Drawdown warning (equity ≈ 130$)")


def scenario_3_drawdown_critical() -> None:
    """
    Scénario 3 : drawdown global CRITICAL.
    - Pertes totales ≳ -30 USD, pour tomber sous 120 USD d'equity.
    Objectif : déclencher GLOBAL_DRAWDOWN_CRITICAL et bloquer live_allowed.
    """
    manager = build_manager("scenario3_dd_critical")

    # Pertes réparties sur plusieurs wallets de trading
    manager.on_trade_closed("sniper_sol", Decimal("-15"))
    manager.on_trade_closed("copy_sol",   Decimal("-10"))
    manager.on_trade_closed("base_main",  Decimal("-10"))

    manager.on_tick()

    print_snapshot(manager, "Scénario 3 — Drawdown CRITICAL (equity ≲ 120$)")


def scenario_4_losing_streak() -> None:
    """
    Scénario 4 : série de pertes consécutives.
    - 4 trades perdants d'affilée sur sniper_sol => WARNING
    - puis 2 trades perdants supplémentaires (6 au total) => CRITICAL
    Pertes petites (ex: -1 USD) pour ne pas mélanger avec les scénarios de drawdown.
    """
    manager = build_manager("scenario4_losing_streak")

    print("\n--- Partie 1 : 4 pertes consécutives sur sniper_sol ---")
    for _ in range(4):
        manager.on_trade_closed("sniper_sol", Decimal("-1"))
    manager.on_tick()
    print_snapshot(manager, "Scénario 4 — Après 4 pertes consécutives (WARNING attendu)")

    print("\n--- Partie 2 : encore 2 pertes consécutives (total = 6) ---")
    for _ in range(2):
        manager.on_trade_closed("sniper_sol", Decimal("-1"))
    manager.on_tick()
    print_snapshot(manager, "Scénario 4 — Après 6 pertes consécutives (CRITICAL attendu)")


# ---------------------------------------------------------------------------
# Main / CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Tests de finance/wallets pour le profil LIVE_150 (PAPER)."
    )
    parser.add_argument(
        "-s",
        "--scenario",
        type=str,
        default="all",
        help="Scénario à exécuter : 0,1,2,3,4 ou 'all' (par défaut).",
    )

    args = parser.parse_args()
    scen = str(args.scenario).lower()

    if scen == "0":
        scenario_0_initial()
    elif scen == "1":
        scenario_1_small_green_day()
    elif scen == "2":
        scenario_2_drawdown_warning()
    elif scen == "3":
        scenario_3_drawdown_critical()
    elif scen == "4":
        scenario_4_losing_streak()
    elif scen == "all":
        scenario_0_initial()
        scenario_1_small_green_day()
        scenario_2_drawdown_warning()
        scenario_3_drawdown_critical()
        scenario_4_losing_streak()
    else:
        raise SystemExit(f"Scénario inconnu : {args.scenario}")


if __name__ == "__main__":
    main()

