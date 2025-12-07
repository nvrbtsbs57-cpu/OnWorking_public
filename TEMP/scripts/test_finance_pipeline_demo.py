#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo FinancePipeline (AutoFees + Sweep + Compounding).

Ce script ne fait qu'afficher des plans logiques.
Aucune requête RPC, aucun transfert réel.
"""

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict

# ======================================================================
# PYTHONPATH
# ======================================================================

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ======================================================================
# Imports projet
# ======================================================================

from bot.finance.pipeline import (
    FinanceConfig,
    FinancePipeline,
    WalletSnapshot,
    TransferPlan,
)


# ======================================================================
# Logging
# ======================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_finance_pipeline_demo")


# ======================================================================
# Helpers
# ======================================================================

def load_global_config() -> Dict:
    cfg_path = BASE_DIR / "config.json"
    if not cfg_path.exists():
        raise SystemExit(f"config.json introuvable: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def print_plans(title: str, plans: list[TransferPlan]) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)
    if not plans:
        print("(aucun plan)")
        return
    for p in plans:
        print(
            f"- [{p.type}] {p.from_wallet} -> {p.to_wallet} | "
            f"native={p.amount_native} | usd={p.amount_usd} | reason={p.reason}"
        )


# ======================================================================
# Main demo
# ======================================================================

def main() -> None:
    cfg = load_global_config()

    wallet_roles = cfg.get("wallet_roles", {})
    wallets_cfg = cfg.get("wallets", [])

    finance_cfg = FinanceConfig.from_global_config(cfg)
    pipeline = FinancePipeline(
        config=finance_cfg,
        wallet_roles=wallet_roles,
        wallets_cfg=wallets_cfg,
    )

    # Snapshots fictifs pour la démo
    snapshots: Dict[str, WalletSnapshot] = {
        # Wallet de scalping SOL avec pas assez de gas
        "sniper_sol": WalletSnapshot(
            name="sniper_sol",
            chain="solana",
            role="SCALPING",
            balance_native=Decimal("0.1"),
            balance_usd=Decimal("1500"),
            realized_profit_usd=Decimal("300"),
            tags=["sniper", "sol"],
        ),
        # Wallet copytrading SOL avec beaucoup de profits
        "copy_sol": WalletSnapshot(
            name="copy_sol",
            chain="solana",
            role="COPYTRADING",
            balance_native=Decimal("0.6"),
            balance_usd=Decimal("800"),
            realized_profit_usd=Decimal("120"),
            tags=["copy", "sol"],
        ),
        # Wallet main BASE
        "base_main": WalletSnapshot(
            name="base_main",
            chain="base",
            role="MAIN",
            balance_native=Decimal("0.005"),
            balance_usd=Decimal("2000"),
            realized_profit_usd=Decimal("80"),
            tags=["main", "base"],
        ),
        # Wallet de fees EVM
        "fees": WalletSnapshot(
            name="fees",
            chain="ethereum",
            role="AUTO_FEES",
            balance_native=Decimal("0.5"),
            balance_usd=Decimal("500"),
            realized_profit_usd=Decimal("0"),
            tags=["fees", "gas"],
        ),
        # Vault
        "vault": WalletSnapshot(
            name="vault",
            chain="ethereum",
            role="SAVINGS",
            balance_native=Decimal("0.0"),
            balance_usd=Decimal("1000"),
            realized_profit_usd=Decimal("0"),
            tags=["vault", "long-term"],
        ),
        # Profits SOL
        "profits_sol": WalletSnapshot(
            name="profits_sol",
            chain="solana",
            role="SAVINGS",
            balance_native=Decimal("0.0"),
            balance_usd=Decimal("300"),
            realized_profit_usd=Decimal("0"),
            tags=["profits", "sol", "vault"],
        ),
    }

    # Plans AutoFees
    autofees_plans = pipeline.plan_autofees(snapshots)
    print_plans("Plans AutoFees", autofees_plans)

    # Plans Sweep profits
    sweep_plans = pipeline.plan_sweep_profits(snapshots)
    print_plans("Plans Sweep profits", sweep_plans)

    # Plans Compounding depuis le vault
    compound_plans = pipeline.plan_compounding(snapshots)
    print_plans("Plans Compounding", compound_plans)

    # Plans globaux
    all_plans = pipeline.plan_all(snapshots)
    print_plans("Plans combinés (plan_all)", all_plans)


if __name__ == "__main__":
    main()
