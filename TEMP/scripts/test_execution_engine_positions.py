from __future__ import annotations

# --- Bootstrap du projet pour que "bot" soit importable ---
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # = C:\Users\ME\Documents\BOT_GODMODE
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ----------------------------------------------------------------------
from decimal import Decimal
from datetime import datetime

from bot.trading.execution import ExecutionEngine, ExecutionRequest, ExecutionResult
from bot.trading.positions import PositionManager
from bot.trading.models import TradeSide
from bot.core.logging import get_logger

logger = get_logger(__name__)


# ----------------------------------------------------------------------
# Mocks simples pour WalletManager et PaperTradingEngine
# ----------------------------------------------------------------------
class FakeWalletManager:
    def choose_wallet_for_trade(
        self,
        chain: str,
        strategy_tag: str,
        notional_usd: float,
        prefer_role=None,
        require_tags=None,
    ) -> str:
        logger.info(
            "FakeWalletManager.choose_wallet_for_trade called",
            extra={
                "chain": chain,
                "strategy_tag": strategy_tag,
                "notional_usd": notional_usd,
            },
        )
        return "W0_paper_test"

    def register_new_open_trade(self, wallet_name: str) -> None:
        logger.info(
            "FakeWalletManager.register_new_open_trade",
            extra={"wallet": wallet_name},
        )


class FakeTrade:
    """
    Objet trade minimal compatible avec Position.from_trade().
    """
    def __init__(self, *, id, chain, symbol, side, qty, price, reason, meta=None):
        self.id = id
        self.chain = chain
        self.symbol = symbol
        self.side = side
        self.qty = qty
        self.price = price
        self.notional = qty * price
        self.opened_at = datetime.utcnow()
        self.reason = reason
        self.meta = meta or {}
        self.wallet_id = None
        self.strategy_id = None
        self.fees = Decimal("0")


class FakePaperTradingEngine:
    def execute_signal(self, signal):
        """
        Reçoit un TradeSignal (de ton vrai module) et retourne un FakeTrade.
        """
        logger.info(
            "FakePaperTradingEngine.execute_signal",
            extra={
                "chain": signal.chain,
                "symbol": signal.symbol,
                "side": signal.side.value if hasattr(signal.side, "value") else str(signal.side),
                "qty": float(signal.qty),
                "price": float(signal.price),
                "reason": signal.reason,
            },
        )
        trade_id = f"paper_trade_{int(datetime.utcnow().timestamp())}"
        return FakeTrade(
            id=trade_id,
            chain=signal.chain,
            symbol=signal.symbol,
            side=signal.side,
            qty=signal.qty,
            price=signal.price,
            reason=signal.reason,
            meta=signal.meta,
        )


class FakeRiskEngine:
    def check_execution_request(self, req: ExecutionRequest, wallet_name: str):
        """
        Retourne (ok, reason, extra).
        Ici : toujours OK, sauf si notional_usd > 10_000.
        """
        if req.notional_usd > Decimal("10000"):
            return False, "notional_too_big_for_fake_risk", {"max": 10000}
        return True, None, {"checked": True}


# ----------------------------------------------------------------------
# Scénario de test principal
# ----------------------------------------------------------------------
def run_test(run_mode: str = "paper") -> None:
    print(f"\n================ TEST ExecutionEngine (mode={run_mode}) ================")

    # 1) Instancier les composants
    wallet_manager = FakeWalletManager()
    paper_engine = FakePaperTradingEngine()
    position_manager = PositionManager()
    risk_engine = FakeRiskEngine()

    engine = ExecutionEngine(
        wallet_manager=wallet_manager,
        paper_engine=paper_engine,
        position_manager=position_manager,
        risk_engine=risk_engine,
        run_mode=run_mode,  # "paper" ou "safe"
    )

    # 2) Construire une ExecutionRequest
    req = ExecutionRequest(
        chain="ethereum",
        symbol="TEST/ETH",
        side=TradeSide.BUY,
        notional_usd=Decimal("100"),      # 100 USD
        limit_price=Decimal("2"),         # => qty = 50
        slippage_bps=200,
        strategy_tag="unit_test_strategy",
        meta={"test_case": "basic_execution"},
    )

    # 3) Exécuter
    result: ExecutionResult = engine.execute(req)

    print("\n=== ExecutionResult ===")
    print("success     :", result.success)
    print("reason      :", result.reason)
    print("used_wallet :", result.used_wallet)
    print("tx_hash     :", result.tx_hash)
    print("extra       :", result.extra)

    # 4) Vérifier les positions ouvertes
    open_positions = position_manager.get_open_positions()
    print("\n=== Open Positions ===")
    if not open_positions:
        print("AUCUNE position ouverte => problème à corriger.")
        return

    for pos in open_positions:
        print(
            f"- pos_id={pos.id} "
            f"wallet={pos.wallet_id} "
            f"chain={pos.chain} symbol={pos.symbol} "
            f"side={getattr(pos.side, 'value', str(pos.side))} "
            f"entry={pos.entry_price} qty_init={pos.initial_qty} qty_rem={pos.remaining_qty} "
            f"tp1={pos.tp1_price} tp2={pos.tp2_price} sl={pos.sl_price}"
        )

    # 5) Simuler un mouvement de prix qui tape le TP1
    pos0 = open_positions[0]
    tp1_price = pos0.tp1_price or (pos0.entry_price * Decimal("1.2"))

    print("\n=== Simulation price tick TP1 ===")
    print("price ->", tp1_price)

    events = position_manager.on_price_tick(
        chain=pos0.chain,
        symbol=pos0.symbol,
        price=tp1_price,
    )

    print("Events générés :", [e.event_type.value for e in events])

    # 6) Appliquer les events (mise à jour remaining_qty, status, realized_pnl)
    position_manager.apply_events(events)

    # 7) Revoir la position
    pos_after = position_manager.get_position(pos0.id)
    if pos_after:
        print("\n=== Position après TP1 ===")
        print(
            f"status={pos_after.status.value} "
            f"remaining_qty={pos_after.remaining_qty} "
            f"realized_pnl={pos_after.realized_pnl} "
            f"tp1_filled={pos_after.tp1_filled} tp2_filled={pos_after.tp2_filled}"
        )
    else:
        print("Position introuvable après events (bug?)")


if __name__ == "__main__":
    # 1) test mode paper classique
    run_test(run_mode="paper")

    # 2) test mode safe (utilise FakeRiskEngine)
    run_test(run_mode="safe")
