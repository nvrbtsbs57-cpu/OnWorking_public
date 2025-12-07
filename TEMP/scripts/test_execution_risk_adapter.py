# scripts/test_execution_risk_adapter.py

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.risk import RiskConfig, RiskEngine, RiskDecision, OrderRiskContext  # type: ignore
from bot.trading.execution_risk_adapter import (
    ExecutionRiskAdapter,
    SimpleWalletStats,
)  # type: ignore


class DummyExecutionEngine:
    """
    Engine d'exécution factice utilisé uniquement pour les tests.

    Il se contente d'afficher ce qu'il recevrait et de renvoyer un dict.
    """

    def execute(self, request):
        print("[DummyExecutionEngine] execute() appelé avec :", request)
        return {
            "status": "executed",
            "executed": True,
            "notional_usd": float(
                request.get("size_usd", request.get("notional_usd", 0.0))
                if isinstance(request, dict)
                else getattr(request, "size_usd", getattr(request, "notional_usd", 0.0))
            ),
        }


def make_risk_engine_from_inline_config() -> RiskEngine:
    """
    Construit un RiskEngine à partir d'une petite config inline (équivalente à config.json["risk"]).
    """

    risk_cfg_dict = {
        "global": {
            "enabled": True,
            "max_global_daily_loss_pct": 10.0,
            "max_consecutive_losing_trades": 3,
        },
        "wallets": {
            "sniper_sol": {
                "max_pct_balance_per_trade": 2.0,
                "max_daily_loss_pct": 5.0,
                "max_open_positions": 5,
                "max_notional_per_asset": 500.0,
            }
        },
    }

    cfg = RiskConfig.from_dict(risk_cfg_dict)
    # Ici on peut simuler le SAFETY_MODE si besoin, ex: "SAFE" / "DEGEN"
    cfg = cfg.adjusted_for_safety("SAFE")
    return RiskEngine(cfg)


def main() -> None:
    print("=== test_execution_risk_adapter ===")

    risk_engine = make_risk_engine_from_inline_config()

    # Stats : wallet avec 10000$ d'equity, PnL ok, peu de pertes, 0 open positions
    stats = SimpleWalletStats(
        default_equity_usd=10000.0,
        default_open_positions=0,
        default_wallet_daily_pnl_pct=0.0,
        default_global_daily_pnl_pct=0.0,
        default_consecutive_losing_trades=0,
    )

    inner = DummyExecutionEngine()
    adapter = ExecutionRiskAdapter(
        inner_engine=inner,
        risk_engine=risk_engine,
        stats_provider=stats,
        enabled=True,
    )

    # ------------------------------------------------------------------
    # 1) Cas ACCEPT (ordre petit par rapport à l'equity)
    # ------------------------------------------------------------------
    print("\n--- Cas ACCEPT ---")
    req_accept = {
        "wallet_id": "sniper_sol",
        "symbol": "ETH",
        "chain": "ethereum",
        "side": "buy",
        "size_usd": Decimal("100.0"),
    }
    res_accept = adapter.execute(req_accept)
    print("Résultat ACCEPT :", res_accept)

    # ------------------------------------------------------------------
    # 2) Cas ADJUST (ordre trop gros pour les limites du wallet)
    # ------------------------------------------------------------------
    print("\n--- Cas ADJUST ---")
    req_adjust = {
        "wallet_id": "sniper_sol",
        "symbol": "ETH",
        "chain": "ethereum",
        "side": "buy",
        "size_usd": Decimal("5000.0"),  # 50% de l'equity, > 2% autorisés
    }
    res_adjust = adapter.execute(req_adjust)
    print("Résultat ADJUST :", res_adjust)

    # ------------------------------------------------------------------
    # 3) Cas REJECT / EJECT (on simule un gros drawdown global)
    # ------------------------------------------------------------------
    print("\n--- Cas EJECT (drawdown global) ---")
    ctx = OrderRiskContext(
        wallet_id="sniper_sol",
        symbol="ethereum:ETH",
        side="buy",
        notional_usd=100.0,
        wallet_equity_usd=10000.0,
        open_positions=0,
        wallet_daily_pnl_pct=0.0,
        global_daily_pnl_pct=-20.0,  # -20% global => > max_global_daily_loss_pct=10%
        consecutive_losing_trades=0,
    )
    decision, size, reason = risk_engine.evaluate_order(ctx)
    print("Decision EJECT directe RiskEngine:", decision, size, reason)


if __name__ == "__main__":
    main()
