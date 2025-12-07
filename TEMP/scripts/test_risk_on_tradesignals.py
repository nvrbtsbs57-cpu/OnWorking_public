#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_risk_on_tradesignals.py

But :
- Charger config.json
- Construire un runtime via build_runtime_from_config()
- Récupérer :
    - risk_engine (RealRiskEngine)
    - wallet_manager (RuntimeWalletManager)
- Construire un TradeSignal d'exemple
- Le convertir en OrderRiskContext en utilisant les métriques wallets
- Appeler risk_engine.evaluate_order(ctx)
- Afficher la décision (ACCEPT / ADJUST / REJECT / EJECT)
"""

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.runtime import build_runtime_from_config  # noqa: E402
from bot.core.signals import TradeSignal, SignalSide, SignalKind  # noqa: E402
from bot.core.risk import OrderRiskContext, RiskDecision  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config() -> Dict[str, Any]:
    cfg_path = BASE_DIR / "config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.json introuvable à {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def compute_daily_pct(pnl_today: Decimal, equity_now: Decimal) -> float:
    """
    pct ~= pnl_today / (equity_now - pnl_today) * 100
    Si base <= 0, on retourne 0.0 pour éviter les divisions foireuses.
    """
    base = equity_now - pnl_today
    if base <= Decimal("0"):
        return 0.0
    return float((pnl_today / base) * Decimal("100"))


def build_order_ctx_from_signal(
    sig: TradeSignal,
    wallet_manager: Any,
) -> OrderRiskContext:
    """
    Construit un OrderRiskContext à partir d'un TradeSignal en lisant
    les métriques dans RuntimeWalletManager.

    NOTE : pour l'instant, open_positions et consecutive_losing_trades
    sont stub (0) — ils seront branchés plus tard quand M7 sera là.
    """
    # Equity & PnL wallet
    wallet_equity = wallet_manager.get_wallet_equity_usd(sig.wallet_id)
    wallet_pnl_today = wallet_manager.get_wallet_pnl_today_usd(sig.wallet_id)

    # Equity & PnL global
    total_equity = wallet_manager.get_total_equity_usd()
    global_pnl_today = wallet_manager.get_global_pnl_today_usd()

    wallet_daily_pct = compute_daily_pct(wallet_pnl_today, wallet_equity)
    global_daily_pct = compute_daily_pct(global_pnl_today, total_equity)

    # Pour l'instant : stubs (pas encore de positions / trade store M7)
    open_positions = 0
    consecutive_losing_trades = 0

    side_str = sig.side.value

    ctx = OrderRiskContext(
        wallet_id=sig.wallet_id,
        symbol=sig.symbol,
        side=side_str,
        notional_usd=sig.notional_usd,
        wallet_equity_usd=float(wallet_equity),
        open_positions=open_positions,
        wallet_daily_pnl_pct=wallet_daily_pct,
        global_daily_pnl_pct=global_daily_pct,
        consecutive_losing_trades=consecutive_losing_trades,
    )
    return ctx


def main() -> None:
    setup_logging()
    log = logging.getLogger("test_risk_on_tradesignals")

    raw_cfg = load_config()
    log.info("Config chargée depuis %s", BASE_DIR / "config.json")

    # Construction du runtime (M1 + M2 + M3)
    config, deps = build_runtime_from_config(raw_cfg)

    risk_engine = deps.risk_engine
    wallet_manager = deps.wallet_manager

    log.info(
        "Runtime construit — mode=%s, safety=%s",
        config.execution_mode.value,
        config.safety_mode.value,
    )

    # Petit snapshot avant de tester
    if hasattr(wallet_manager, "debug_snapshot"):
        log.info("Snapshot initial des wallets : %s", wallet_manager.debug_snapshot())

    # ------------------------------------------------------------------
    # Construction d'un TradeSignal d'exemple
    # ------------------------------------------------------------------
    sig = TradeSignal(
        id="test-001",
        strategy_id="test_manual",
        wallet_id="sniper_sol",  # doit exister dans ta config
        symbol="FAKE/USDC",
        side=SignalSide.BUY,
        notional_usd=100.0,
        kind=SignalKind.ENTRY,
        meta={"note": "test risk on tradesignal"},
    )

    log.info(
        "Signal test : id=%s wallet=%s symbol=%s side=%s notional=%.2f",
        sig.id,
        sig.wallet_id,
        sig.symbol,
        sig.side.value,
        sig.notional_usd,
    )

    # ------------------------------------------------------------------
    # Construction d'un OrderRiskContext et appel RiskEngine.evaluate_order
    # ------------------------------------------------------------------
    ctx = build_order_ctx_from_signal(sig, wallet_manager)

    decision, size_accepted, reason = risk_engine.evaluate_order(ctx)

    log.info(
        "Résultat RiskEngine : decision=%s size_accepted=%.2f reason=%s",
        decision.value,
        size_accepted,
        reason,
    )

    if decision is RiskDecision.EJECT:
        log.warning("⚠️ Le moteur est en mode EJECT (circuit breaker).")
    elif decision is RiskDecision.REJECT:
        log.warning("⚠️ Ordre rejeté par le moteur de risque.")
    elif decision is RiskDecision.ADJUST:
        log.info("✅ Ordre accepté mais ajusté par le moteur de risque.")
    elif decision is RiskDecision.ACCEPT:
        log.info("✅ Ordre accepté tel quel par le moteur de risque.")


if __name__ == "__main__":
    main()
