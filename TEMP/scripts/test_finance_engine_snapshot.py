#!/usr/bin/env python
"""
Test FinanceEngine (M4-core) : agrégats globaux et par rôle.

Scénario :
- charge config.json à la racine,
- construit un RuntimeWalletManager + WalletFlowsEngine interne,
- instancie un FinanceEngine "papier" au-dessus,
- simule un peu de PnL sur plusieurs wallets,
- construit un FinanceSnapshot et log :
    * equity totale,
    * PnL du jour total,
    * fees du jour,
    * agrégats par rôle,
    * détail par wallet.
"""

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

# ----------------------------------------------------------------------
# Bootstrapping du PYTHONPATH pour trouver le package "bot"
# ----------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[1]  # C:\Users\ME\Documents\BOT_GODMODE

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Imports projet
from bot.wallets.runtime_manager import RuntimeWalletManager  # type: ignore
from bot.wallets.engine import WalletFlowsEngine  # type: ignore
from bot.finance.engine import (  # type: ignore
    FinanceEngine,
    FinanceEngineConfig,
    FinanceSnapshot,
)


LOGGER = logging.getLogger("test_finance_engine_snapshot")


def load_config() -> Dict[str, Any]:
    cfg_path = PROJECT_ROOT / "config.json"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"config.json introuvable à {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def log_snapshot(snapshot: FinanceSnapshot) -> None:
    LOGGER.info("===== FINANCE SNAPSHOT as_of=%s =====", snapshot.as_of.isoformat())
    LOGGER.info(
        "TOTALS — equity=%s USD | pnl_today=%s USD | fees_today=%s USD",
        snapshot.total_equity_usd,
        snapshot.total_pnl_today_usd,
        snapshot.total_fees_today_usd,
    )

    # Agrégats par rôle
    LOGGER.info("--- Equity par rôle ---")
    for role, eq in snapshot.equity_by_role.items():
        LOGGER.info("role=%s | equity=%s", role.name if hasattr(role, "name") else str(role), eq)

    LOGGER.info("--- PnL du jour par rôle ---")
    for role, pnl in snapshot.pnl_today_by_role.items():
        LOGGER.info("role=%s | pnl_today=%s", role.name if hasattr(role, "name") else str(role), pnl)

    LOGGER.info("--- Fees du jour par rôle ---")
    for role, fees in snapshot.fees_today_by_role.items():
        LOGGER.info("role=%s | fees_today=%s", role.name if hasattr(role, "name") else str(role), fees)

    # Détail par wallet
    LOGGER.info("--- Détail wallets ---")
    for w in snapshot.wallets:
        LOGGER.info(
            "wallet=%s | role=%s | balance=%s | pnl_today=%s | gross_pnl_today=%s | fees_today=%s",
            w.wallet_id,
            w.role.name if hasattr(w.role, "name") else str(w.role),
            w.balance_usd,
            w.realized_pnl_today_usd,
            w.gross_pnl_today_usd,
            w.fees_paid_today_usd,
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )

    raw_cfg = load_config()

    # Construction WalletFlowsEngine via RuntimeWalletManager
    wallet_logger = logging.getLogger("RuntimeWalletManager")
    manager = RuntimeWalletManager.from_config(raw_cfg, logger=wallet_logger)
    flows_engine: WalletFlowsEngine = manager.engine  # type: ignore[assignment]

    # FinanceEngine "papier" au-dessus du WalletFlowsEngine
    fin_cfg = FinanceEngineConfig(
        enable_auto_fees=True,
        enable_profit_split=True,
        enable_compounding=False,
    )
    finance_logger = logging.getLogger("FinanceEngine")
    finance_engine = FinanceEngine(
        wallet_engine=flows_engine,
        cfg=fin_cfg,
        pipeline=None,
        logger=finance_logger,
    )

    # ------------------------------------------------------------------
    # Simu PnL : on applique quelques PnL sur différents wallets
    # ------------------------------------------------------------------
    LOGGER.info("=== Simulation PnL sur plusieurs wallets ===")
    flows_engine.apply_realized_pnl("sniper_sol", Decimal("250"))   # PnL positif
    flows_engine.apply_realized_pnl("copy_sol", Decimal("80"))      # PnL positif
    flows_engine.apply_realized_pnl("base_main", Decimal("-120"))   # PnL négatif

    # On laisse le moteur appliquer autofees + profit-split
    flows_engine.run_finance_cycle_all()

    # ------------------------------------------------------------------
    # Construction du snapshot finance
    # ------------------------------------------------------------------
    snapshot = finance_engine.build_snapshot()
    log_snapshot(snapshot)

    LOGGER.info("Test FinanceEngine terminé.")


if __name__ == "__main__":
    main()
