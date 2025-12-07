# scripts/test_execution_with_risk_adapter.py

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict

from bot.core.risk import RiskConfig, RiskEngine
from bot.trading.execution import ExecutionRequest, ExecutionResult
from bot.trading.execution_risk_adapter import (
    ExecutionRiskAdapter,
    SimpleWalletStats,
    KillSwitchState,
    to_execution_result,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Dummy engine pour tester uniquement le wrapper ExecutionRiskAdapter
# ----------------------------------------------------------------------


class DummyExecutionEngine:
    """
    Engine minimal pour tester le pipeline RiskAdapter / KillSwitch.

    - logge l'exécution,
    - renvoie un ExecutionResult(success=True, ...).
    """

    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        logger.info(
            "[DUMMY_ENGINE] EXECUTE wallet=%s symbol=%s side=%s notional=%s",
            req.wallet_id,
            req.symbol,
            req.side,
            req.notional_usd,
        )
        return ExecutionResult(
            success=True,
            reason="executed_by_dummy_engine",
            used_wallet=req.wallet_id,
            tx_hash=None,
            extra={
                "engine": "dummy",
                "symbol": req.symbol,
                "side": req.side.value if hasattr(req.side, "value") else str(req.side),
                "notional_usd": float(req.notional_usd),
            },
        )


# ----------------------------------------------------------------------
# Helpers RiskEngine + Adapter
# ----------------------------------------------------------------------


def make_risk_engine() -> RiskEngine:
    """
    Construit un RiskEngine avec une config simple embarquée dans le script.
    """
    risk_cfg_dict: Dict[str, Any] = {
        "global": {
            "enabled": True,
            "max_global_daily_loss_pct": 10.0,
            "max_consecutive_losing_trades": 5,
        },
        "wallets": {
            "sniper_sol": {
                "max_pct_balance_per_trade": 5.0,  # 5% equity max
                "max_daily_loss_pct": 5.0,         # -5% max / jour
                "max_open_positions": 10,
                "max_notional_per_asset": 0.0,     # pas de cap absolu ici
            }
        },
    }

    cfg = RiskConfig.from_dict(risk_cfg_dict)
    # Pour le test on reste en mode NORMAL (tu peux mettre SAFE/DEGEN si tu veux voir la diff)
    cfg = cfg.adjusted_for_safety("NORMAL")

    return RiskEngine(config=cfg)


def build_adapter_for_stats(
    stats: SimpleWalletStats,
    *,
    kill_switch: KillSwitchState | None = None,
) -> ExecutionRiskAdapter:
    """
    Construit un ExecutionRiskAdapter avec :
      - DummyExecutionEngine comme inner_engine,
      - RiskEngine réel,
      - SimpleWalletStats comme provider,
      - KillSwitchState optionnel.
    """
    risk_engine = make_risk_engine()
    inner_engine = DummyExecutionEngine()

    adapter = ExecutionRiskAdapter(
        inner_engine=inner_engine,
        risk_engine=risk_engine,
        stats_provider=stats,
        enabled=True,
        kill_switch=kill_switch,
    )
    return adapter


def make_request(notional: Decimal) -> ExecutionRequest:
    """
    Crée un ExecutionRequest de test pour le wallet 'sniper_sol'.
    """
    return ExecutionRequest(
        chain="solana",
        symbol="SOL/USDC",
        side=type("Side", (), {"value": "buy"})(),  # petit hack pour éviter d'importer TradeSide
        notional_usd=notional,
        limit_price=None,
        slippage_bps=0,
        wallet_id="sniper_sol",
        strategy_tag="test_execution_with_risk",
    )


# ----------------------------------------------------------------------
# Scénarios
# ----------------------------------------------------------------------


def scenario_accept() -> None:
    """
    Trade normal, dans les limites => ACCEPT.
    """
    logger.info("=== SCENARIO: ACCEPT ===")

    stats = SimpleWalletStats(
        default_equity_usd=1_000.0,          # equity wallet
        default_wallet_daily_pnl_pct=0.0,    # pnl jour neutre
        default_global_daily_pnl_pct=0.0,
        default_open_positions=0,
        default_consecutive_losing_trades=0,
    )

    adapter = build_adapter_for_stats(stats)
    req = make_request(Decimal("20"))  # 2% de 1000 => OK pour max_pct_balance_per_trade=5%

    raw = adapter.execute(req)
    result = to_execution_result(raw, request=req)

    logger.info(
        "ACCEPT result: success=%s reason=%s extra=%s",
        result.success,
        result.reason,
        result.extra,
    )


def scenario_adjust() -> None:
    """
    Taille trop grosse par rapport à max_pct_balance_per_trade => ADJUST.
    """
    logger.info("=== SCENARIO: ADJUST ===")

    stats = SimpleWalletStats(
        default_equity_usd=1_000.0,
        default_wallet_daily_pnl_pct=0.0,
        default_global_daily_pnl_pct=0.0,
        default_open_positions=0,
        default_consecutive_losing_trades=0,
    )

    adapter = build_adapter_for_stats(stats)
    req = make_request(Decimal("200"))  # 20% de 1000 > 5% => réduction attendue

    raw = adapter.execute(req)
    result = to_execution_result(raw, request=req)

    logger.info(
        "ADJUST result: success=%s reason=%s extra=%s",
        result.success,
        result.reason,
        result.extra,
    )


def scenario_reject_daily_loss() -> None:
    """
    PnL jour du wallet trop bas => REJECT.
    """
    logger.info("=== SCENARIO: REJECT (wallet daily loss) ===")

    stats = SimpleWalletStats(
        default_equity_usd=1_000.0,
        default_wallet_daily_pnl_pct=-6.0,   # <= -5% => REJECT
        default_global_daily_pnl_pct=0.0,
        default_open_positions=0,
        default_consecutive_losing_trades=0,
    )

    adapter = build_adapter_for_stats(stats)
    req = make_request(Decimal("50"))

    raw = adapter.execute(req)
    result = to_execution_result(raw, request=req)

    logger.info(
        "REJECT result: success=%s reason=%s extra=%s",
        result.success,
        result.reason,
        result.extra,
    )


def scenario_eject_and_killswitch() -> None:
    """
    Perte globale trop forte => EJECT + KillSwitch activé,
    puis deuxième ordre bloqué par KillSwitch.
    """
    logger.info("=== SCENARIO: EJECT + KILL_SWITCH ===")

    # Global daily pnl très mauvais pour forcer un EJECT global
    stats = SimpleWalletStats(
        default_equity_usd=1_000.0,
        default_wallet_daily_pnl_pct=0.0,
        default_global_daily_pnl_pct=-15.0,  # <= -10% => EJECT (max_global_daily_loss_pct=10)
        default_open_positions=0,
        default_consecutive_losing_trades=0,
    )

    kill_switch = KillSwitchState(
        enabled=True,
        trip_on_risk_eject=True,
        manual_tripped=False,
    )

    adapter = build_adapter_for_stats(stats, kill_switch=kill_switch)
    req = make_request(Decimal("50"))

    # 1er ordre -> EJECT (risk) + KillSwitch.tripped
    raw1 = adapter.execute(req)
    result1 = to_execution_result(raw1, request=req)

    logger.info(
        "EJECT result: success=%s reason=%s extra=%s kill_switch_active=%s kill_switch_reason=%s",
        result1.success,
        result1.reason,
        result1.extra,
        kill_switch.is_active(),
        kill_switch.reason,
    )

    # 2e ordre -> bloqué directement par KillSwitch
    raw2 = adapter.execute(req)
    result2 = to_execution_result(raw2, request=req)

    logger.info(
        "KILL_SWITCH result: success=%s reason=%s extra=%s kill_switch_active=%s",
        result2.success,
        result2.reason,
        result2.extra,
        kill_switch.is_active(),
    )

    # Reset du KillSwitch pour montrer la remise à zéro
    kill_switch.reset()

    raw3 = adapter.execute(req)
    result3 = to_execution_result(raw3, request=req)

    logger.info(
        "AFTER RESET result: success=%s reason=%s extra=%s kill_switch_active=%s",
        result3.success,
        result3.reason,
        result3.extra,
        kill_switch.is_active(),
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main() -> None:
    scenario_accept()
    scenario_adjust()
    scenario_reject_daily_loss()
    scenario_eject_and_killswitch()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)8s | %(name)s | %(message)s",
    )
    main()
