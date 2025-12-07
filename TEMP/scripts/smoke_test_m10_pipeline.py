#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, Tuple, Optional

from bot.core.logging import get_logger
from bot.wallets.runtime_manager import RuntimeWalletManager
from bot.trading.execution import ExecutionEngine
from bot.trading.paper_trader import PaperTrader, PaperTraderConfig

logger = get_logger("smoke_test_m10_pipeline")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.json"


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        print(f"[FATAL] config.json introuvable: {CONFIG_PATH}")
        sys.exit(1)
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    print("=== M10 – SMOKE TEST PIPELINE (LIVE_150 / PAPER) ===")

    # 1) Charger la config
    cfg = load_config()
    print("[OK] config.json chargé")

    # 2) RuntimeWalletManager
    try:
        rwm = RuntimeWalletManager.from_config(cfg)
        total_eq = rwm.get_total_equity_usd()
        print(f"[OK] RuntimeWalletManager initialisé, equity_total_usd={total_eq}")
    except Exception as exc:
        print("[FAIL] RuntimeWalletManager.from_config():", repr(exc))
        sys.exit(1)

    # 3) PaperTrader + ExecutionEngine
    try:
        pt_cfg = PaperTraderConfig.from_env()
        paper_trader = PaperTrader(config=pt_cfg)
        exec_engine = ExecutionEngine(
            inner_engine=paper_trader,
            wallet_manager=rwm,
        )
        print("[OK] ExecutionEngine(PAPER) initialisé")
    except Exception as exc:
        print("[FAIL] init ExecutionEngine / PaperTrader:", repr(exc))
        sys.exit(1)

    # 4) Trade fictif pour tester tout le cheminement
    dummy_signal = SimpleNamespace(
        chain="solana",
        symbol="SOL/USDC",
        side="buy",  # PaperTrader._normalize_side gère "buy"/"sell"
        notional_usd=Decimal("5"),
        entry_price=Decimal("1"),
        meta={"source": "smoke_test_m10", "strategy": "smoke_test"},
    )

    try:
        trade = exec_engine.execute_signal(dummy_signal)
        print(
            f"[OK] Trade simulé via ExecutionEngine: "
            f"id={trade.id} chain={trade.chain} symbol={trade.symbol} "
            f"side={trade.side.value} notional={trade.notional}"
        )
    except Exception as exc:
        print("[FAIL] exec_engine.execute_signal(dummy_signal):", repr(exc))
        sys.exit(1)

    # 5) Lecture du store & PnL (sanity check)
    try:
        recent = paper_trader.get_recent_trades(limit=3)
        print(f"[OK] TradeStore accessible, derniers trades = {len(recent)}")
    except Exception as exc:
        print("[WARN] Impossible de lire les trades récents:", repr(exc))

    try:
        pnl = paper_trader.get_pnl()
        if pnl is not None:
            print(f"[OK] PnL calculé (total) = {pnl.total}")
        else:
            print("[OK] PnL encore None (pas de trades ou première initialisation)")
    except Exception as exc:
        print("[WARN] compute_pnl a échoué:", repr(exc))

    print("=== SMOKE TEST TERMINÉ ===")


if __name__ == "__main__":
    main()

