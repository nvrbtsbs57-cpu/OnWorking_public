# scripts/test_finance_profit_split.py

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
import os
import sys

# --- Hack chemin projet (comme tes autres scripts) --------------------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.wallets.models import (  # type: ignore
    WalletRole,
    WalletConfig,
    WalletFlowsConfig,
    ProfitSplitRule,
)
from bot.wallets.engine import WalletFlowsEngine  # type: ignore


def build_test_engine(logger: logging.Logger) -> WalletFlowsEngine:
    """
    Construit un WalletFlowsEngine minimal avec :
      - W1 : wallet de trading memecoins
      - W0 : vault de sécurisation
      - 1 règle de profit-split : si W1 gagne >= 10%, on envoie 50% du profit vers W0.
    """
    wallet_cfgs = [
        WalletConfig(
            id="W1",
            role=WalletRole.TRADE_MEMECOINS,
            chain="SOL",
            base_ccy="USDC",
            initial_balance_usd=Decimal("1000"),
            min_balance_usd=Decimal("0"),
            max_risk_pct_per_trade=Decimal("5"),
            max_daily_loss_pct=Decimal("20"),
            allow_outflows=True,
        ),
        WalletConfig(
            id="W0",
            role=WalletRole.VAULT,
            chain="SOL",
            base_ccy="USDC",
            initial_balance_usd=Decimal("0"),
            min_balance_usd=Decimal("0"),
            max_risk_pct_per_trade=Decimal("0"),
            max_daily_loss_pct=None,
            allow_outflows=False,
        ),
    ]

    flows_cfg = WalletFlowsConfig(
        auto_fees_wallet_id=None,
        min_auto_fees_pct=Decimal("0"),
        max_auto_fees_pct=Decimal("0"),
        compounding_enabled=False,
        compounding_interval_days=3,
        profit_split_rules=[
            ProfitSplitRule(
                source_wallet_id="W1",
                target_wallet_id="W0",
                trigger_pct=Decimal("10"),       # déclenche à partir de +10% de gain
                percent_of_profit=Decimal("50"),  # envoie 50% du profit vers W0
            )
        ],
    )

    engine = WalletFlowsEngine(
        wallet_configs=wallet_cfgs,
        flows_config=flows_cfg,
        logger=logger.getChild("WalletFlowsEngine"),
    )
    return engine


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("test_finance_profit_split")

    engine = build_test_engine(logger)

    logger.info("=== ÉTAT INITIAL ===")
    logger.info("snapshot=%s", engine.debug_snapshot())

    # ------------------------------------------------------------------
    # 1) On simule un trade gagnant sur W1 : +200 USD (soit +20% sur 1000)
    # ------------------------------------------------------------------
    logger.info("=== APPLIQUER PnL RÉALISÉ SUR W1 (+200 USD) ===")
    engine.apply_realized_pnl(wallet_id="W1", realized_pnl_usd=Decimal("200"))

    logger.info("snapshot après PnL (avant profit-split)=%s", engine.debug_snapshot())

    # ------------------------------------------------------------------
    # 2) On lance les tâches périodiques pour déclencher le profit-split
    # ------------------------------------------------------------------
    logger.info("=== run_periodic_tasks() (devrait déclencher profit-split) ===")
    engine.run_periodic_tasks(now=datetime.utcnow())

    logger.info("snapshot après run_periodic_tasks=%s", engine.debug_snapshot())

    logger.info("Test terminé. Vérifier que :")
    logger.info("- W1 a perdu une partie de son profit (balance ~1100)")
    logger.info("- W0 a reçu la moitié du profit (balance ~100)")


if __name__ == "__main__":
    main()
