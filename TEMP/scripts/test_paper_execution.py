#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Test autonome du moteur PaperTrader :
- instancie PaperTrader (qui crée son TradeStore interne)
- envoie un BUY puis un SELL
- affiche les trades + le PnL + les trades récents
"""

from __future__ import annotations

import sys
from pathlib import Path
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.trading.paper_trader import PaperTrader, PaperTraderConfig, TradeSignal
from bot.trading.models import TradeSide


def dump_obj(label: str, obj) -> None:
    print(label)
    print("  repr :", repr(obj))
    if hasattr(obj, "__dict__"):
        print("  fields:")
        for k, v in obj.__dict__.items():
            print(f"    - {k}: {v}")


def main() -> None:
    print("[test_paper_execution] Initialisation…")

    config = PaperTraderConfig.from_env()
    print(
        "[test_paper_execution] Config PaperTrader : "
        f"path={config.path}, max_trades={config.max_trades}, "
        f"default_chain={config.default_chain}, default_symbol={config.default_symbol}"
    )

    trader = PaperTrader(config=config)
    print("[test_paper_execution] PaperTrader initialisé.")

    # BUY
    buy_signal = TradeSignal(
        chain=config.default_chain,
        symbol=config.default_symbol,
        side=TradeSide.BUY,
        notional_usd=Decimal("20"),
        entry_price=Decimal("2000"),
        meta={"note": "BUY test_paper_execution"},
    )

    print("[test_paper_execution] Envoi BUY signal…")
    buy_trade = trader.execute_signal(buy_signal)
    dump_obj("[test_paper_execution] BUY exécuté :", buy_trade)

    # SELL
    sell_signal = TradeSignal(
        chain=config.default_chain,
        symbol=config.default_symbol,
        side=TradeSide.SELL,
        notional_usd=Decimal("20"),
        entry_price=Decimal("2100"),
        meta={"note": "SELL test_paper_execution"},
    )

    print("[test_paper_execution] Envoi SELL signal…")
    sell_trade = trader.execute_signal(sell_signal)
    dump_obj("[test_paper_execution] SELL exécuté :", sell_trade)

    # PnL
    pnl = trader.get_pnl()
    if pnl is None:
        print("[test_paper_execution] PnL : None (aucun calcul retourné)")
    else:
        dump_obj("[test_paper_execution] Résultat PnL (PaperTrader) :", pnl)

    # Trades récents
    recent = trader.get_recent_trades(limit=10) or []
    print("[test_paper_execution] Trades récents retournés :", len(recent))
    if recent:
        dump_obj("[test_paper_execution] Dernier trade dans le store :", recent[-1])

    print("[test_paper_execution] Terminé.")


if __name__ == "__main__":
    main()
