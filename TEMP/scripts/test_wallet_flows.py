#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_wallet_flows.py

Test simple du WalletManager + WalletFlowManager :

- charge config.json
- construit un WalletManager via WalletManager.from_config()
- simule du PnL journalier sur quelques wallets (sniper_sol, base_main, etc.)
- appelle WalletFlowManager.plan_daily_profit_sweeps()
- affiche les plans de transferts générés

Usage :

    (venv) python scripts/test_wallet_flows.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import sys

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.wallet.manager import WalletManager  # type: ignore
from bot.wallet.flows import WalletFlowManager  # type: ignore


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config.json introuvable à {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg_path = BASE_DIR / "config.json"
    cfg = load_config(cfg_path)

    wm = WalletManager.from_config(cfg)
    print(f"[test_wallet_flows] Wallets chargés : {wm.list_wallets()}")

    # ------------------------------------------------------------------
    # Simulation de PnL journalier sur quelques wallets
    # ------------------------------------------------------------------
    # Adapter ces noms si besoin selon ton config.json :
    test_pnl_values = {
        "sniper_sol": 350.0,   # wallet SCALPING Solana
        "base_main": 120.0,    # MAIN sur Base
        "bsc_main": 80.0,      # sous le seuil => pas de sweep
    }

    for name, pnl_usd in test_pnl_values.items():
        st = wm.get_wallet_state(name)
        if st:
            st.daily_pnl_usd = pnl_usd
            print(f"[test_wallet_flows] {name}: daily_pnl_usd simulé = {pnl_usd} USD")
        else:
            print(f"[test_wallet_flows] WARNING: wallet '{name}' introuvable, skip")

    # ------------------------------------------------------------------
    # WalletFlowManager : génération des plans de sweeps
    # ------------------------------------------------------------------
    flow_mgr = WalletFlowManager(wallet_manager=wm, raw_config=cfg)
    plans = flow_mgr.plan_daily_profit_sweeps()

    print("\n=== PLANS DE SWEEP DE PROFITS ===")
    if not plans:
        print("(aucun plan généré — vérifie les daily_pnl_usd simulés et wallet_roles)")
    else:
        for p in plans:
            print(
                f"- {p.from_wallet} -> {p.to_wallet} "
                f"(chain={p.chain}) : {p.amount_usd:.2f} USD "
                f"[reason={p.reason}, meta={p.meta}]"
            )


if __name__ == "__main__":
    main()
