#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# --- Préparation du sys.path pour que "bot" soit importable même lancé en script ---
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- Imports projet ---
from bot.core.logging import get_logger  # type: ignore
from bot.strategies.memecoin_farming import runtime as meme_runtime  # type: ignore
from bot.core.risk import (  # type: ignore
    RiskConfig,
    RiskEngine,
    OrderRiskContext,
    RiskDecision,
)

log = get_logger("test_m10_risk_auto_trip_v2")


def _build_risk_engine_from_config() -> RiskEngine:
    """
    Construit un RiskEngine à partir de config.json, avec le même
    SAFETY_MODE que le runtime M10.
    """
    cfg: dict[str, Any] = meme_runtime.load_config()
    meme_runtime.setup_logging_from_config(cfg)

    risk_raw = cfg.get("risk", {}) or {}
    safety_mode = str(cfg.get("SAFETY_MODE", "NORMAL")).upper()

    risk_cfg = RiskConfig.from_dict(risk_raw).adjusted_for_safety(safety_mode)
    engine = RiskEngine(config=risk_cfg)

    log.info(
        "RiskEngine construit depuis config.json "
        "(SAFETY_MODE=%s, max_global_daily_loss_pct=%.4f, "
        "max_consecutive_losing_trades=%d)",
        safety_mode,
        risk_cfg.global_cfg.max_global_daily_loss_pct,
        risk_cfg.global_cfg.max_consecutive_losing_trades,
    )
    return engine


def _test_global_drawdown_eject(engine: RiskEngine) -> None:
    """
    Cas 1 : drawdown global de la journée dépasse max_global_daily_loss_pct
    => RiskDecision.EJECT attendu.
    """
    max_loss_pct = float(engine.config.global_cfg.max_global_daily_loss_pct or 0.0)
    if max_loss_pct <= 0.0:
        log.warning(
            "max_global_daily_loss_pct <= 0 dans la config (%.4f), "
            "Impossible de tester l'EJECT global.",
            max_loss_pct,
        )
        return

    # On force un PnL global du jour inférieur à -max_loss_pct
    global_daily_pnl_pct = -(max_loss_pct + 1.0)  # ex: max=2.5 -> -3.5%

    ctx = OrderRiskContext(
        wallet_id="sniper_sol",
        symbol="SOL/USDC",
        side="buy",
        notional_usd=10.0,
        wallet_equity_usd=30.0,
        open_positions=0,
        wallet_daily_pnl_pct=0.0,
        global_daily_pnl_pct=global_daily_pnl_pct,
        consecutive_losing_trades=0,
    )

    decision, size_usd, reason = engine.evaluate_order(ctx)

    log.info(
        "[DRAWNDOWN] decision=%s size_usd=%.4f reason=%r "
        "| daily_drawdown_pct=%s soft_stop=%s hard_stop=%s",
        decision,
        size_usd,
        reason,
        engine.daily_drawdown_pct,
        engine.soft_stop_active,
        engine.hard_stop_active,
    )

    if decision != RiskDecision.EJECT:
        log.error(
            "Echec test EJECT: attendu decision=EJECT, obtenu=%s "
            "(global_daily_pnl_pct=%.4f, max_loss_pct=%.4f)",
            decision,
            global_daily_pnl_pct,
            max_loss_pct,
        )
        raise SystemExit(1)

    if not engine.hard_stop_active:
        log.error(
            "Echec test EJECT: hard_stop_active devrait être True après EJECT "
            "(daily_drawdown_pct=%s, max_loss_pct=%.4f)",
            engine.daily_drawdown_pct,
            max_loss_pct,
        )
        raise SystemExit(1)

    log.info("Test EJECT sur drawdown global: OK.")


def _test_consecutive_losing_trades_adjust(engine: RiskEngine) -> None:
    """
    Cas 2 : série de trades perdants >= max_consecutive_losing_trades
    => RiskDecision.ADJUST attendu, avec taille réduite.
    """
    max_streak = int(engine.config.global_cfg.max_consecutive_losing_trades or 0)
    if max_streak <= 0:
        log.warning(
            "max_consecutive_losing_trades <= 0 (=%d), "
            "Impossible de tester l'ADJUST sur losing streak.",
            max_streak,
        )
        return

    notional = 10.0
    wallet_equity = 30.0

    ctx = OrderRiskContext(
        wallet_id="sniper_sol",
        symbol="SOL/USDC",
        side="buy",
        notional_usd=notional,
        wallet_equity_usd=wallet_equity,
        open_positions=0,
        wallet_daily_pnl_pct=0.0,
        global_daily_pnl_pct=0.0,  # drawdown global neutre ici
        consecutive_losing_trades=max_streak,
    )

    decision, size_usd, reason = engine.evaluate_order(ctx)

    log.info(
        "[LOSING_STREAK] decision=%s size_usd=%.4f reason=%r",
        decision,
        size_usd,
        reason,
    )

    if decision != RiskDecision.ADJUST:
        log.error(
            "Echec test ADJUST: attendu decision=ADJUST, obtenu=%s "
            "(consecutive_losing_trades=%d, max=%d)",
            decision,
            ctx.consecutive_losing_trades,
            max_streak,
        )
        raise SystemExit(1)

    expected_size = notional * 0.5
    if abs(size_usd - expected_size) > 1e-6:
        log.error(
            "Echec test ADJUST: taille attendue=%.4f, obtenue=%.4f",
            expected_size,
            size_usd,
        )
        raise SystemExit(1)

    log.info("Test ADJUST sur losing streak: OK.")


def main() -> None:
    log.info("=== M10 – test_m10_risk_auto_trip_v2 (RiskEngine core) ===")
    engine = _build_risk_engine_from_config()

    _test_global_drawdown_eject(engine)
    _test_consecutive_losing_trades_adjust(engine)

    log.info("Tous les tests RiskEngine M10 (auto-trip) sont OK.")
    log.info("test_m10_risk_auto_trip_v2 terminé.")


if __name__ == "__main__":
    main()

