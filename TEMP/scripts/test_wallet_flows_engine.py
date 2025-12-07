#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script de test pour bot.wallets.WalletFlowsEngine

- Charge config.json
- Construit le moteur via build_wallet_engine_from_config()
- Affiche un snapshot des wallets
- Simule quelques demandes de trade + fills
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# ----------------------------------------------------------------------
# PYTHONPATH pour retrouver le package "bot"
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ----------------------------------------------------------------------
# Imports projet
# ----------------------------------------------------------------------

from bot.wallets.factory import build_wallet_engine_from_config
from bot.wallets.models import TradeRiskRequest


# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


logger = logging.getLogger("test_wallet_flows_engine")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _print_snapshot(engine) -> None:
    """
    Affiche un snapshot lisible des wallets (balance + PnL) pour debug.
    """
    states = engine.states
    logger.info("Snapshot wallets (%d):", len(states))
    for wid, state in states.items():
        logger.info(
            "  - %s | balance=%.2f | realized_pnl_today=%.2f | gross_pnl_today=%.2f | fees_today=%.2f | losing_streak=%d",
            wid,
            float(state.balance_usd),
            float(state.realized_pnl_today_usd),
            float(state.gross_pnl_today_usd),
            float(state.fees_paid_today_usd),
            state.consecutive_losing_trades,
        )


# ----------------------------------------------------------------------
# main()
# ----------------------------------------------------------------------


def main() -> None:
    setup_logging()

    cfg_path = BASE_DIR / "config.json"
    if not cfg_path.exists():
        print(f"[FATAL] Fichier de config introuvable: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    with cfg_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    logger.info("Config chargée depuis %s", cfg_path)

    # Construction du WalletFlowsEngine depuis la config
    engine = build_wallet_engine_from_config(raw_cfg, logger=logger)
    logger.info("WalletFlowsEngine initialisé avec wallets: %s", list(engine.states.keys()))

    # Snapshot initial
    _print_snapshot(engine)

    wallet_ids = list(engine.states.keys())
    if not wallet_ids:
        logger.error("Aucun wallet configuré, test interrompu.")
        return

    test_wallet_id = wallet_ids[0]
    logger.info("Wallet utilisé pour le test: %s", test_wallet_id)

    # ------------------------------------------------------------------
    # 1) Test d'une demande de trade
    # ------------------------------------------------------------------
    now = datetime.utcnow()
    req = TradeRiskRequest(
        wallet_id=test_wallet_id,
        requested_notional_usd=Decimal("100"),
        timestamp=now,
        symbol="TEST/USDC",
    )

    decision = engine.evaluate_trade_request(req)
    logger.info(
        "TradeRiskDecision: approved=%s, max_allowed_notional_usd=%.2f, reason=%s",
        decision.approved,
        float(decision.max_allowed_notional_usd),
        decision.reason,
    )

    # ------------------------------------------------------------------
    # 2) Simuler un trade gagnant
    # ------------------------------------------------------------------
    logger.info("Simulation d'un trade gagnant (+25 USD, fees 1 USD).")
    engine.register_fill(
        wallet_id=test_wallet_id,
        realized_pnl_usd=Decimal("25"),
        fees_paid_usd=Decimal("1"),
    )
    _print_snapshot(engine)

    # ------------------------------------------------------------------
    # 3) Simuler un trade perdant
    # ------------------------------------------------------------------
    logger.info("Simulation d'un trade perdant (-15 USD, fees 0.5 USD).")
    engine.register_fill(
        wallet_id=test_wallet_id,
        realized_pnl_usd=Decimal("-15"),
        fees_paid_usd=Decimal("0.5"),
    )
    _print_snapshot(engine)

    # ------------------------------------------------------------------
    # 4) Tick périodique (reset / compounding / auto-fees stub)
    # ------------------------------------------------------------------
    logger.info("Appel de run_periodic_tasks() (reset journalier + hooks stub).")
    engine.run_periodic_tasks()
    _print_snapshot(engine)

    logger.info("Test WalletFlowsEngine terminé.")


if __name__ == "__main__":
    main()
