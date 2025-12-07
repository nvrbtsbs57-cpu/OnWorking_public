#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Test du moteur d'exécution PAPER :

ExecutionEngine → PaperTrader → TradeStore → PnL

Scénario :
  - instancie PaperTrader + ExecutionEngine
  - envoie un BUY via ExecutionEngine
  - envoie un SELL via ExecutionEngine
  - affiche les ExecutionResult, le PnL et les trades récents
"""

from __future__ import annotations

import sys
from pathlib import Path
from decimal import Decimal

# ======================================================================
# Fix du PYTHONPATH pour pouvoir faire `import bot.*`
# ======================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ======================================================================
# Imports projet
# ======================================================================

from bot.trading.paper_trader import PaperTrader, PaperTraderConfig
from bot.trading.execution import ExecutionEngine, ExecutionRequest
from bot.trading.models import TradeSide


def dump_obj(label: str, obj) -> None:
    """Affiche proprement un dataclass / objet sans supposer les attributs exacts."""
    print(label)
    print("  repr :", repr(obj))
    if hasattr(obj, "__dict__"):
        print("  fields:")
        for k, v in obj.__dict__.items():
            print(f"    - {k}: {v}")


def main() -> None:
    print("[test_execution_engine] Initialisation…")

    # ------------------------------------------------------------------
    # 1) Config PaperTrader + instances
    # ------------------------------------------------------------------
    pt_config = PaperTraderConfig.from_env()
    print(
        "[test_execution_engine] Config PaperTrader : "
        f"path={pt_config.path}, max_trades={pt_config.max_trades}, "
        f"default_chain={pt_config.default_chain}, default_symbol={pt_config.default_symbol}"
    )

    paper_trader = PaperTrader(config=pt_config)
    exec_engine = ExecutionEngine(paper_trader=paper_trader)

    print("[test_execution_engine] PaperTrader + ExecutionEngine initialisés.")

    # ------------------------------------------------------------------
    # 2) BUY via ExecutionEngine
    # ------------------------------------------------------------------
    buy_req = ExecutionRequest(
        chain=pt_config.default_chain,
        symbol=pt_config.default_symbol,
        side=TradeSide.BUY,
        notional_usd=Decimal("20"),       # 20 USD de notionnel
        limit_price=Decimal("2000"),      # prix de référence
        slippage_bps=0,                   # pas de slippage simulé
        wallet_id="W0:main",
        strategy_tag="test_exec_engine",
        meta={"note": "BUY via ExecutionEngine test"},
    )

    print("[test_execution_engine] Envoi BUY request…")
    buy_result = exec_engine.execute(buy_req)
    dump_obj("[test_execution_engine] Résultat BUY (ExecutionResult) :", buy_result)

    # ------------------------------------------------------------------
    # 3) SELL via ExecutionEngine
    # ------------------------------------------------------------------
    sell_req = ExecutionRequest(
        chain=pt_config.default_chain,
        symbol=pt_config.default_symbol,
        side=TradeSide.SELL,
        notional_usd=Decimal("20"),       # on revend le même notionnel
        limit_price=Decimal("2100"),
        slippage_bps=0,
        wallet_id="W0:main",
        strategy_tag="test_exec_engine",
        meta={"note": "SELL via ExecutionEngine test"},
    )

    print("[test_execution_engine] Envoi SELL request…")
    sell_result = exec_engine.execute(sell_req)
    dump_obj("[test_execution_engine] Résultat SELL (ExecutionResult) :", sell_result)

    # ------------------------------------------------------------------
    # 4) PnL global (via PaperTrader)
    # ------------------------------------------------------------------
    pnl = paper_trader.get_pnl()
    if pnl is None:
        print("[test_execution_engine] PnL : None (aucun calcul retourné)")
    else:
        dump_obj("[test_execution_engine] PnL global (PaperTrader) :", pnl)

    # ------------------------------------------------------------------
    # 5) Trades récents dans le TradeStore
    # ------------------------------------------------------------------
    recent_trades = paper_trader.get_recent_trades(limit=10) or []
    print("[test_execution_engine] Trades récents retournés :", len(recent_trades))
    if recent_trades:
        dump_obj(
            "[test_execution_engine] Dernier trade dans le store :",
            recent_trades[-1],
        )

    print("[test_execution_engine] Terminé.")


if __name__ == "__main__":
    main()
