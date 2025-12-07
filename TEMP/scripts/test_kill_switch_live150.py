from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Tuple, Optional

# ----------------------------------------------------------------------
# Bootstrap du projet (comme dans tes autres scripts)
# ----------------------------------------------------------------------

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(ROOT_DIR)  # remonte d'un cran pour avoir le repo
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# ----------------------------------------------------------------------
# Imports projet
# ----------------------------------------------------------------------

from bot.core.risk import RiskDecision
from bot.trading.models import TradeSide
from bot.trading.execution_risk_adapter import (
    ExecutionRiskAdapter,
    SimpleWalletStats,
    KillSwitchState,
)

logger = logging.getLogger("test_kill_switch_live150")
logging.basicConfig(level=logging.INFO)


# ----------------------------------------------------------------------
# Doubles pour tester l'adapter sans toucher au vrai moteur d'exécution
# ----------------------------------------------------------------------


@dataclass
class DummyRequest:
    wallet_id: str
    symbol: str
    side: TradeSide
    notional_usd: Decimal
    chain: str = "solana"


class DummyInnerEngine:
    """
    Engine d'exécution très simple : compte les exécutions.
    """

    def __init__(self) -> None:
        self.last_request: Optional[DummyRequest] = None
        self.executions: int = 0

    def execute(self, request: DummyRequest) -> dict:
        self.last_request = request
        self.executions += 1
        return {
            "status": "executed",
            "executed": True,
            "symbol": request.symbol,
            "wallet_id": request.wallet_id,
            "side": request.side.value
            if isinstance(request.side, TradeSide)
            else str(request.side),
            "notional_usd": float(request.notional_usd),
        }


class FakeRiskEngine:
    """
    RiskEngine fake :
    - prend une liste de (decision, allowed_usd, reason)
    - à chaque appel, renvoie l'entrée suivante.
    """

    def __init__(
        self,
        decisions: List[Tuple[RiskDecision, float, str]],
    ) -> None:
        self._decisions = decisions
        self.calls = 0

    def evaluate_order(self, ctx: Any):
        if self.calls < len(self._decisions):
            decision, allowed, reason = self._decisions[self.calls]
        else:
            decision, allowed, reason = self._decisions[-1]
        self.calls += 1
        return decision, allowed, reason


def _make_adapter(
    decisions: List[Tuple[RiskDecision, float, str]],
    kill_switch: KillSwitchState,
) -> Tuple[ExecutionRiskAdapter, DummyInnerEngine]:
    """
    Helper pour instancier un adapter complet :
      - DummyInnerEngine
      - FakeRiskEngine
      - SimpleWalletStats avec un peu d'equity
      - KillSwitchState fourni
    """
    inner = DummyInnerEngine()
    risk_engine = FakeRiskEngine(decisions)
    stats = SimpleWalletStats(default_equity_usd=100.0)

    adapter = ExecutionRiskAdapter(
        inner_engine=inner,
        risk_engine=risk_engine,
        stats_provider=stats,
        enabled=True,
        kill_switch=kill_switch,
    )
    return adapter, inner


def _make_request(notional: float = 5.0) -> DummyRequest:
    return DummyRequest(
        wallet_id="sniper_sol",
        symbol="SOL/USDC",
        side=TradeSide.BUY,
        notional_usd=Decimal(str(notional)),
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_manual_kill_switch():
    """
    Cas 1 : kill-switch manuel = on doit bloquer tout de suite,
    sans appeler le RiskEngine ni l'inner_engine.
    """
    ks = KillSwitchState(
        enabled=True,
        trip_on_risk_eject=True,
        manual_tripped=True,
    )

    decisions = [(RiskDecision.ACCEPT, 100.0, "should_not_be_used")]
    adapter, inner = _make_adapter(decisions, ks)

    req = _make_request(5.0)
    res = adapter.execute(req)

    assert res["status"] == "risk_kill_switch", res
    assert res["executed"] is False
    assert inner.executions == 0
    print("[OK] test_manual_kill_switch")


def test_trip_on_risk_eject():
    """
    Cas 2 : kill-switch activé + trip_on_risk_eject = True.
    - 1er ordre => RiskDecision.EJECT -> kill-switch se trippe.
    - 2ème ordre => bloqué par kill-switch direct.
    """
    ks = KillSwitchState(
        enabled=True,
        trip_on_risk_eject=True,
        manual_tripped=False,
    )

    decisions = [
        (RiskDecision.EJECT, 0.0, "global_drawdown"),  # premier appel
        (RiskDecision.ACCEPT, 100.0, "ok"),            # deuxième appel
    ]

    adapter, inner = _make_adapter(decisions, ks)

    # 1) premier appel => EJECT, kill-switch se trippe
    res1 = adapter.execute(_make_request(5.0))
    assert res1["status"] == "risk_eject", res1
    assert res1["executed"] is False
    assert ks.tripped is True
    assert ks.is_active() is True
    assert inner.executions == 0  # aucun ordre réel

    # 2) deuxième appel => doit être bloqué par kill-switch AVANT risk_engine
    res2 = adapter.execute(_make_request(5.0))
    assert res2["status"] == "risk_kill_switch", res2
    assert res2["executed"] is False
    # toujours aucune exécution réelle
    assert inner.executions == 0

    print("[OK] test_trip_on_risk_eject")


def test_eject_without_kill_switch():
    """
    Cas 3 : kill-switch désactivé (enabled=False).
    - un EJECT ne doit PAS tripper le kill-switch.
    - on reste sur le comportement "risk_eject" simple.
    """
    ks = KillSwitchState(
        enabled=False,           # désactivé
        trip_on_risk_eject=True,
        manual_tripped=False,
    )

    decisions = [
        (RiskDecision.EJECT, 0.0, "global_limit"),     # 1er appel
        (RiskDecision.ACCEPT, 100.0, "ok_after"),      # 2e appel
    ]

    adapter, inner = _make_adapter(decisions, ks)

    res1 = adapter.execute(_make_request(5.0))
    assert res1["status"] == "risk_eject", res1
    assert ks.tripped is False
    assert ks.is_active() is False
    assert inner.executions == 0

    # Maintenant le risk engine dit ACCEPT, et le kill-switch est inactif
    res2 = adapter.execute(_make_request(5.0))
    assert res2["status"] == "executed", res2
    assert res2["executed"] is True
    assert inner.executions == 1

    print("[OK] test_eject_without_kill_switch")


def main():
    test_manual_kill_switch()
    test_trip_on_risk_eject()
    test_eject_without_kill_switch()
    print("Tous les tests kill-switch sont OK ✅")


if __name__ == "__main__":
    main()
