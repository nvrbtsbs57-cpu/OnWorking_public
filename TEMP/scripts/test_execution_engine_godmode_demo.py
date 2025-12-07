#!/usr/bin/env python
"""
Test rapide du pipeline GODMODE :
WalletManager (fake) + RiskEngine (fake) + ExecutionEngine (PAPER).

On vérifie juste que la "glue" fonctionne :
- choix du wallet
- contrôle de risque
- exécution papier
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

# ======================================================================
# Ajout de la racine du projet au PYTHONPATH
# ======================================================================

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[1]          # ...\BOT_GODMODE
NESTED_ROOT = PROJECT_ROOT / "BOT_GODMODE"   # ...\BOT_GODMODE\BOT_GODMODE (au cas où)

for root in (PROJECT_ROOT, NESTED_ROOT):
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))

# ======================================================================
# Imports du projet
# ======================================================================

from bot.trading.execution import ExecutionEngine, ExecutionRequest, ExecutionResult
from bot.trading.models import TradeSide
from bot.trading.positions import PositionManager

# ======================================================================
# Logging
# ======================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_execution_engine_godmode_demo")


# ======================================================================
# Fakes pour le test
# ======================================================================

class FakeWalletManager:
    """
    Version ultra simple d'un WalletManager :
    - choisit toujours le wallet "sniper_sol"
    """

    def choose_wallet_for_trade(
        self,
        chain: str,
        strategy_tag: str,
        notional_usd: float,
        prefer_role: Optional[str] = None,
        require_tags: Optional[list[str]] = None,
    ) -> Optional[str]:
        logger.info(
            "[FakeWalletManager] choose_wallet_for_trade "
            f"(chain={chain}, strategy={strategy_tag}, notional={notional_usd})"
        )
        return "sniper_sol"


class FakeRiskEngine:
    """
    Fake RiskEngine pour tester la glue ExecutionEngine.

    Règle simple :
      - notional_usd <= max_notional  => OK
      - notional_usd >  max_notional  => rejeté
    """

    def __init__(self, max_notional: float = 10_000.0) -> None:
        self.max_notional = max_notional

    def check_execution_request(
        self,
        req: ExecutionRequest,
        wallet_name: str,
    ) -> tuple[bool, Optional[str], Dict[str, Any]]:
        notional = float(req.notional_usd)
        logger.info(
            "[FakeRiskEngine] check_execution_request "
            f"(wallet={wallet_name}, notional={notional})"
        )
        if notional <= self.max_notional:
            return True, None, {"max_notional": self.max_notional}
        else:
            return (
                False,
                "notional_too_large",
                {"max_notional": self.max_notional, "requested": notional},
            )


@dataclass
class FakeTrade:
    """
    Objet minimal retourné par FakePaperEngine.execute_signal().
    Il imite ce dont ExecutionEngine a besoin :
    id, chain, symbol, side, notional.
    """

    id: str
    chain: str
    symbol: str
    side: TradeSide
    notional: Decimal


class FakePaperEngine:
    """
    Moteur d'exécution papier minimaliste.
    """

    def __init__(self) -> None:
        self._next_id = 1
        self.trades: list[FakeTrade] = []

    def execute_signal(self, signal) -> FakeTrade:
        """
        `signal` est un bot.trading.models.TradeSignal dans le vrai code.
        Ici on ne lit que les attributs utilisés par ExecutionEngine.
        """
        trade_id = f"T{self._next_id}"
        self._next_id += 1

        trade = FakeTrade(
            id=trade_id,
            chain=signal.chain,
            symbol=signal.symbol,
            side=signal.side,
            notional=signal.notional_usd,
        )
        self.trades.append(trade)

        logging.info(
            "[FakePaperEngine] execute_signal -> trade_id=%s chain=%s "
            "symbol=%s side=%s notional=%s",
            trade.id,
            trade.chain,
            trade.symbol,
            trade.side.value,
            trade.notional,
        )
        return trade


# ======================================================================
# Scénarios de test
# ======================================================================

def run_scenario_small_trade(engine: ExecutionEngine) -> None:
    """
    Trade qui doit passer : notional_usd faible, accepté par FakeRiskEngine.
    """
    req = ExecutionRequest(
        chain="solana",
        symbol="SOL",
        side=TradeSide.BUY,
        notional_usd=Decimal("1000"),
        limit_price=Decimal("100"),
        slippage_bps=50,
        wallet_id=None,  # sera choisi par FakeWalletManager
        strategy_tag="scalping_test",
        meta={"comment": "small trade - should pass"},
    )

    logger.info("=== Scenario 1: small trade (should PASS) ===")
    result: ExecutionResult = engine.execute(req)

    logger.info("ExecutionResult: %s", result)
    print("\n[Scenario 1] SUCCESS:", result.success)
    print("  reason     :", result.reason)
    print("  used_wallet:", result.used_wallet)
    print("  extra      :", result.extra)


def run_scenario_large_trade(engine: ExecutionEngine) -> None:
    """
    Trade qui doit être rejeté par le FakeRiskEngine (notional trop gros).
    """
    req = ExecutionRequest(
        chain="solana",
        symbol="SOL",
        side=TradeSide.BUY,
        notional_usd=Decimal("50000"),
        limit_price=Decimal("100"),
        slippage_bps=50,
        wallet_id=None,  # sera choisi par FakeWalletManager
        strategy_tag="degen_test",
        meta={"comment": "large trade - should be blocked by risk"},
    )

    logger.info("=== Scenario 2: large trade (should FAIL by risk) ===")
    result: ExecutionResult = engine.execute(req)

    logger.info("ExecutionResult: %s", result)
    print("\n[Scenario 2] SUCCESS:", result.success)
    print("  reason     :", result.reason)
    print("  used_wallet:", result.used_wallet)
    print("  extra      :", result.extra)


# ======================================================================
# Entrée principale
# ======================================================================

def main() -> None:
    # Fakes pour ce test
    wallet_manager = FakeWalletManager()
    risk_engine = FakeRiskEngine(max_notional=10_000.0)
    paper_engine = FakePaperEngine()
    position_manager = PositionManager()

    # run_mode="safe" => le risk_engine est appelé
    engine = ExecutionEngine(
        wallet_manager=wallet_manager,
        paper_engine=paper_engine,
        position_manager=position_manager,
        risk_engine=risk_engine,
        run_mode="safe",
    )

    run_scenario_small_trade(engine)
    run_scenario_large_trade(engine)


if __name__ == "__main__":
    main()
