# scripts/test_finance_pipeline_plans.py

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict

# ---------------------------------------------------------------------------
# Assurer que le package "bot" est importable
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.finance.pipeline import (  # type: ignore
    FinanceConfig,
    FinancePipeline,
    WalletSnapshot,
)

logger = logging.getLogger("test_finance_pipeline_plans")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build_example_snapshots() -> Dict[str, WalletSnapshot]:
    """Construit un set de WalletSnapshot purement papier pour tester le pipeline."""
    snaps: Dict[str, WalletSnapshot] = {}

    def add(
        name: str,
        chain: str,
        balance_native: str,
        balance_usd: str,
        realized_profit_usd: str,
    ) -> None:
        snaps[name] = WalletSnapshot(
            name=name,
            chain=chain,
            balance_native=Decimal(str(balance_native)),
            balance_usd=Decimal(str(balance_usd)),
            realized_profit_usd=Decimal(str(realized_profit_usd)),
        )

    # Wallets de trading / copy (profits + gas un peu faibles pour tester autofees/sweep)
    add(
        name="sniper_sol",
        chain="solana",
        balance_native="0.05",   # < min_gas 0.3 -> doit déclencher autofees
        balance_usd="1500",
        realized_profit_usd="400",  # > min_profit_usd=50 -> sweep
    )
    add(
        name="copy_sol",
        chain="solana",
        balance_native="0.01",   # < min_gas 0.3 -> doit déclencher autofees
        balance_usd="800",
        realized_profit_usd="120",
    )
    add(
        name="base_main",
        chain="base",
        balance_native="0.001",  # < min_gas 0.01 -> autofees
        balance_usd="1200",
        realized_profit_usd="260",
    )
    add(
        name="bsc_main",
        chain="bsc",
        balance_native="0.001",  # < min_gas 0.01 -> autofees
        balance_usd="900",
        realized_profit_usd="55",
    )

    # Wallet de fees (gas provider EVM + fallback AUTO_FEES)
    add(
        name="fees",
        chain="ethereum",
        balance_native="0.5",   # de quoi alimenter les autres
        balance_usd="1000",
        realized_profit_usd="0",
    )

    # Wallets de profits / savings
    add(
        name="profits_sol",
        chain="solana",
        balance_native="0",
        balance_usd="0",
        realized_profit_usd="0",
    )
    add(
        name="profits_base",
        chain="base",
        balance_native="0",
        balance_usd="0",
        realized_profit_usd="0",
    )
    add(
        name="profits_bsc",
        chain="bsc",
        balance_native="0",
        balance_usd="0",
        realized_profit_usd="0",
    )

    # Vault (long-term)
    add(
        name="vault",
        chain="ethereum",
        balance_native="0",
        balance_usd="5000",  # assez pour compounding
        realized_profit_usd="0",
    )

    # Emergency (backup, normalement pas touché par le pipeline)
    add(
        name="emergency",
        chain="ethereum",
        balance_native="0",
        balance_usd="1000",
        realized_profit_usd="0",
    )

    return snaps


def log_plans(title: str, plans) -> None:
    logger.info("=== %s — %d TransferPlan ===", title, len(plans))
    if not plans:
        return
    for p in plans:
        logger.info(
            "[%-9s] from=%s -> to=%s chain=%s native=%s usd=%s reason=%s",
            p.type,
            p.from_wallet,
            p.to_wallet,
            p.chain,
            str(p.amount_native),
            str(p.amount_usd),
            p.reason,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )

    cfg_path = BASE_DIR / "config.json"

    logger.info("Chargement de la config globale depuis %s", cfg_path)
    with cfg_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    # Config finance (autofees / sweep / compounding) depuis config.json
    finance_cfg = FinanceConfig.from_global_config(raw_cfg)

    wallet_roles = raw_cfg.get("wallet_roles", {}) or {}
    wallets_cfg = raw_cfg.get("wallets", []) or []

    pipeline = FinancePipeline(
        config=finance_cfg,
        wallet_roles=wallet_roles,
        wallets_cfg=wallets_cfg,
    )

    # Snapshots purement papier (USD/gas/profits fictifs)
    snapshots = build_example_snapshots()

    # Tests ciblés
    plans_autofees = pipeline.plan_autofees(snapshots)
    log_plans("plan_autofees", plans_autofees)

    plans_sweep = pipeline.plan_sweep_profits(snapshots)
    log_plans("plan_sweep_profits", plans_sweep)

    plans_comp = pipeline.plan_compounding(snapshots)
    log_plans("plan_compounding", plans_comp)

    # Planning global
    plans_all = pipeline.plan_all(snapshots)
    log_plans("plan_all", plans_all)

    logger.info("Test FinancePipeline terminé.")


if __name__ == "__main__":
    main()

