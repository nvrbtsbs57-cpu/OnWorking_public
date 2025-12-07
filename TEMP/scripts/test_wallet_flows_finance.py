#!/usr/bin/env python
"""
Test ciblé WalletFlowsEngine (M4-core) : auto-fees + profit splits.

Scénario :
- charge config.json à la racine du projet,
- construit un RuntimeWalletManager + récupère son WalletFlowsEngine interne,
- affiche un snapshot initial,
- applique un PnL positif sur un wallet de trading (ex: sniper_sol),
- laisse WalletFlowsEngine appliquer auto-fees + profit-split,
- affiche les snapshots après chaque étape.

Les effets visibles dépendent de ta config :
- auto_fees_wallet_id, min/max_auto_fees_pct,
- profit_split_rules,
- allow_outflows / min_balance_usd par wallet, etc.
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

from bot.wallets.runtime_manager import RuntimeWalletManager  # type: ignore
from bot.wallets.engine import WalletFlowsEngine  # type: ignore


LOGGER = logging.getLogger("test_wallet_flows_finance")


def load_config() -> Dict[str, Any]:
    cfg_path = PROJECT_ROOT / "config.json"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"config.json introuvable à {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def print_snapshot(label: str, engine: WalletFlowsEngine) -> None:
    snap = engine.debug_snapshot()
    LOGGER.info("===== SNAPSHOT: %s =====", label)
    for wid, data in snap.items():
        LOGGER.info(
            "wallet=%s | balance=%s | pnl_today=%s | gross_pnl_today=%s | fees_today=%s | losing_streak=%s",
            wid,
            data.get("balance_usd"),
            data.get("realized_pnl_today_usd"),
            data.get("gross_pnl_today_usd"),
            data.get("fees_paid_today_usd"),
            data.get("consecutive_losing_trades"),
        )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )

    raw_cfg = load_config()

    # Construction du RuntimeWalletManager + WalletFlowsEngine interne
    wallet_logger = logging.getLogger("RuntimeWalletManager")
    manager = RuntimeWalletManager.from_config(raw_cfg, logger=wallet_logger)
    engine = manager.engine  # type: ignore[assignment]

    # Choix du wallet de test : on prend sniper_sol qui existe déjà dans tes logs.
    test_wallet_id = "sniper_sol"

    LOGGER.info("=== Test WalletFlowsEngine sur wallet_id=%s ===", test_wallet_id)

    # Snapshot initial
    print_snapshot("INITIAL", engine)

    # ------------------------------------------------------------------
    # Étape 1 : PnL positif modéré (ex: +200 USD)
    # ------------------------------------------------------------------
    pnl1 = Decimal("200")
    LOGGER.info(
        "--- Étape 1: apply_realized_pnl(wallet_id=%s, pnl_usd=%s) ---",
        test_wallet_id,
        pnl1,
    )
    engine.apply_realized_pnl(test_wallet_id, pnl1)

    # On force un cycle finance global pour être sûr d'avoir auto-fees + splits
    engine.run_finance_cycle_all()

    print_snapshot("APRES_PNL_+200", engine)

    # ------------------------------------------------------------------
    # Étape 2 : PnL positif supplémentaire (ex: +800 USD)
    # ------------------------------------------------------------------
    pnl2 = Decimal("800")
    LOGGER.info(
        "--- Étape 2: apply_realized_pnl(wallet_id=%s, pnl_usd=%s) ---",
        test_wallet_id,
        pnl2,
    )
    engine.apply_realized_pnl(test_wallet_id, pnl2)
    engine.run_finance_cycle_all()

    print_snapshot("APRES_PNL_+800_SUPP", engine)

    # ------------------------------------------------------------------
    # Étape 3 : PnL négatif (ex: -150 USD) pour voir le losing_streak
    # ------------------------------------------------------------------
    pnl3 = Decimal("-150")
    LOGGER.info(
        "--- Étape 3: apply_realized_pnl(wallet_id=%s, pnl_usd=%s) ---",
        test_wallet_id,
        pnl3,
    )
    engine.apply_realized_pnl(test_wallet_id, pnl3)
    engine.run_finance_cycle_all()

    print_snapshot("APRES_PNL_-150", engine)

    LOGGER.info("Test WalletFlowsEngine terminé.")


if __name__ == "__main__":
    main()
