#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_risk_engine.py

Petit test du moteur de risque global (bot.core.risk) à partir de config.json.

- charge config.json
- construit RiskConfig.from_dict(config["risk"])
- applique éventuellement SAFETY_MODE (SAFE / NORMAL / DEGEN)
- crée quelques OrderRiskContext de test
- affiche les décisions du moteur
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.risk import RiskConfig, RiskEngine, OrderRiskContext, RiskDecision  # type: ignore


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    cfg_path = BASE_DIR / "config.json"
    cfg = load_config(cfg_path)

    risk_raw = cfg.get("risk", {})
    safety_mode = str(cfg.get("SAFETY_MODE", "normal")).upper()

    risk_cfg = RiskConfig.from_dict(risk_raw)
    adj_cfg = risk_cfg.adjusted_for_safety(safety_mode)

    engine = RiskEngine(adj_cfg)

    print("[test_risk_engine] SAFETY_MODE =", safety_mode)
    print("[test_risk_engine] Global config :", adj_cfg.global_cfg)
    print("[test_risk_engine] Wallets config :", list(adj_cfg.wallets.keys()))

    # On prend quelques wallets présents dans ton config.json :
    # "sniper_sol", "base_main", "bsc_main", etc.
    test_contexts = [
        # Trade raisonnable sur sniper_sol
        OrderRiskContext(
            wallet_id="sniper_sol",
            symbol="ETHUSDT",
            side="buy",
            notional_usd=200.0,
            wallet_equity_usd=10000.0,
            open_positions=5,
            wallet_daily_pnl_pct=1.0,
            global_daily_pnl_pct=-1.0,
            consecutive_losing_trades=0,
        ),
        # Trade trop gros pour le wallet (doit être ADJUST)
        OrderRiskContext(
            wallet_id="sniper_sol",
            symbol="ETHUSDT",
            side="buy",
            notional_usd=5000.0,
            wallet_equity_usd=10000.0,
            open_positions=2,
            wallet_daily_pnl_pct=0.0,
            global_daily_pnl_pct=-2.0,
            consecutive_losing_trades=1,
        ),
        # Wallet en grosse perte journalière (doit être REJECT)
        OrderRiskContext(
            wallet_id="base_main",
            symbol="ETHUSDT",
            side="buy",
            notional_usd=200.0,
            wallet_equity_usd=5000.0,
            open_positions=3,
            wallet_daily_pnl_pct=-20.0,  # sous max_daily_loss_pct du wallet
            global_daily_pnl_pct=-3.0,
            consecutive_losing_trades=2,
        ),
        # Global en forte perte (doit EJECT)
        OrderRiskContext(
            wallet_id="bsc_main",
            symbol="ETHUSDT",
            side="buy",
            notional_usd=200.0,
            wallet_equity_usd=5000.0,
            open_positions=1,
            wallet_daily_pnl_pct=-1.0,
            global_daily_pnl_pct=-50.0,  # << max_global_daily_loss_pct en SAFE
            consecutive_losing_trades=0,
        ),
    ]

    print("\n=== TEST RISK ENGINE ===")
    for ctx in test_contexts:
        decision, size_usd, reason = engine.evaluate_order(ctx)
        print(
            f"- wallet={ctx.wallet_id:10s} symbol={ctx.symbol:7s} "
            f"side={ctx.side:4s} notional_req={ctx.notional_usd:8.2f} USD "
            f"=> decision={decision.value.upper():7s} size_allowed={size_usd:8.2f} USD "
            f"reason={reason}"
        )


if __name__ == "__main__":
    main()
