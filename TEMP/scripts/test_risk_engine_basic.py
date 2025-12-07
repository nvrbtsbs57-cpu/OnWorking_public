#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_risk_engine_basic.py

Tests unitaires simples pour le RiskEngine :

- charge config.json
- construit un RiskEngine pour chaque SAFETY_MODE (SAFE / NORMAL / DEGEN)
- affiche les limites effectives (globales + par wallet)
- simule quelques ordres pour tester :
    * EJECT (perte globale journalière trop forte)
    * REJECT (perte journalière sur un wallet)
    * ADJUST (taille trop grande vs % max par trade)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.risk import (  # type: ignore
    OrderRiskContext,
    RiskConfig,
    RiskDecision,
    RiskEngine,
)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"config.json introuvable à {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_engine_for_safety(raw_cfg: Dict[str, Any], safety_mode: str) -> RiskEngine:
    risk_raw = raw_cfg.get("risk", {}) or {}
    risk_cfg = RiskConfig.from_dict(risk_raw).adjusted_for_safety(safety_mode)
    logging.getLogger("test_risk_engine_basic").info(
        "RiskConfig construit pour SAFETY_MODE=%s", safety_mode
    )
    return RiskEngine(risk_cfg)


def dump_limits(engine: RiskEngine, safety_mode: str) -> None:
    log = logging.getLogger("test_risk_engine_basic")
    rcfg = engine.config

    log.info("=== SAFETY_MODE=%s ===", safety_mode)
    log.info(
        "Global: enabled=%s, max_global_daily_loss_pct=%.2f, max_consecutive_losing_trades=%d",
        rcfg.global_cfg.enabled,
        rcfg.global_cfg.max_global_daily_loss_pct,
        rcfg.global_cfg.max_consecutive_losing_trades,
    )

    for wid, wcfg in rcfg.wallets.items():
        log.info(
            "Wallet %-12s -> max_pct_balance_per_trade=%.2f%%, "
            "max_daily_loss_pct=%.2f%%, max_open_positions=%d, "
            "max_notional_per_asset=%.2f",
            wid,
            wcfg.max_pct_balance_per_trade,
            wcfg.max_daily_loss_pct,
            wcfg.max_open_positions,
            wcfg.max_notional_per_asset,
        )


# ----------------------------------------------------------------------
# Scénarios de test de décisions
# ----------------------------------------------------------------------


def scenario_global_eject(engine: RiskEngine) -> None:
    """
    Test du circuit breaker global : perte journalière globale trop forte.
    """
    log = logging.getLogger("scenario_global_eject")
    gmax = engine.config.global_cfg.max_global_daily_loss_pct

    ctx = OrderRiskContext(
        wallet_id="sniper_sol",
        symbol="SOLUSDT",
        side="buy",
        notional_usd=100.0,
        wallet_equity_usd=1000.0,
        open_positions=0,
        wallet_daily_pnl_pct=0.0,
        global_daily_pnl_pct=-(gmax + 1.0),  # 1% sous la limite
        consecutive_losing_trades=0,
    )

    decision, size, reason = engine.evaluate_order(ctx)
    log.info(
        "Decision=%s size=%.2f reason=%s (attendu: EJECT, taille 0)",
        decision.value,
        size,
        reason,
    )


def scenario_wallet_daily_loss_reject(engine: RiskEngine) -> None:
    """
    Test REJECT : perte journalière trop forte sur un wallet.
    """
    log = logging.getLogger("scenario_wallet_daily_loss_reject")

    wcfg = engine.config.wallets.get("sniper_sol")
    if not wcfg:
        log.warning("Wallet 'sniper_sol' introuvable dans RiskConfig, scenario skip.")
        return

    ctx = OrderRiskContext(
        wallet_id="sniper_sol",
        symbol="SOLUSDT",
        side="buy",
        notional_usd=100.0,
        wallet_equity_usd=1000.0,
        open_positions=0,
        wallet_daily_pnl_pct=-(wcfg.max_daily_loss_pct + 1.0),
        global_daily_pnl_pct=-1.0,  # au-dessus de la limite globale pour voir le check wallet
        consecutive_losing_trades=0,
    )

    decision, size, reason = engine.evaluate_order(ctx)
    log.info(
        "Decision=%s size=%.2f reason=%s (attendu: REJECT, taille 0)",
        decision.value,
        size,
        reason,
    )


def scenario_adjust_by_pct_of_equity(engine: RiskEngine) -> None:
    """
    Test ADJUST : taille demandée trop grande vs max_pct_balance_per_trade.
    """
    log = logging.getLogger("scenario_adjust_by_pct_of_equity")

    wcfg = engine.config.wallets.get("sniper_sol")
    if not wcfg:
        log.warning("Wallet 'sniper_sol' introuvable dans RiskConfig, scenario skip.")
        return

    equity = 1000.0
    requested = equity  # 100% de l'equity
    ctx = OrderRiskContext(
        wallet_id="sniper_sol",
        symbol="SOLUSDT",
        side="buy",
        notional_usd=requested,
        wallet_equity_usd=equity,
        open_positions=0,
        wallet_daily_pnl_pct=0.0,
        global_daily_pnl_pct=0.0,
        consecutive_losing_trades=0,
    )

    decision, size, reason = engine.evaluate_order(ctx)
    log.info(
        "Decision=%s requested=%.2f size_accepted=%.2f (attendu: ADJUST, ~%.2f%% de %.2f)",
        decision.value,
        requested,
        size,
        wcfg.max_pct_balance_per_trade,
        equity,
    )
    if decision is not RiskDecision.ADJUST:
        log.warning("⚠️ Le moteur n'a pas renvoyé ADJUST comme attendu.")


def main() -> None:
    setup_logging()
    logger = logging.getLogger("test_risk_engine_basic")

    cfg_path = BASE_DIR / "config.json"
    raw_cfg = load_config(cfg_path)
    logger.info("Config chargée depuis %s", cfg_path)

    # 1) Aperçu des limites pour SAFE / NORMAL / DEGEN
    for mode in ("SAFE", "NORMAL", "DEGEN"):
        engine = build_engine_for_safety(raw_cfg, mode)
        dump_limits(engine, mode)

    # 2) Scénarios fonctionnels sur le mode NORMAL (le plus "neutre")
    logger.info("\n=== Scénarios de décision (mode NORMAL) ===")
    engine_normal = build_engine_for_safety(raw_cfg, "NORMAL")

    scenario_global_eject(engine_normal)
    scenario_wallet_daily_loss_reject(engine_normal)
    scenario_adjust_by_pct_of_equity(engine_normal)


if __name__ == "__main__":
    main()
