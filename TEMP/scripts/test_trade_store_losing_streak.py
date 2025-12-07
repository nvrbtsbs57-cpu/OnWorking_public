#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/test_trade_store_losing_streak.py

But :
- Charger config.json
- Construire runtime (build_runtime_from_config)
- Créer un RiskAwareExecutionEngine dédié au test avec son propre TradeStore
- Simuler N trades perdants pour faire monter la losing streak
- Envoyer un TradeSignal "final" et voir que le RiskEngine ADJUST (taille réduite)
  si max_consecutive_losing_trades > 0
"""

from __future__ import annotations

import json
import logging
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.core.runtime import build_runtime_from_config  # noqa: E402
from bot.core.execution import RiskAwareExecutionEngine  # noqa: E402
from bot.core.trade_store import TradeStore  # noqa: E402
from bot.core.signals import TradeSignal, SignalSide, SignalKind  # noqa: E402
from bot.core.risk import RiskDecision  # noqa: E402


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def load_config() -> Dict[str, Any]:
    cfg_path = BASE_DIR / "config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.json introuvable à {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> None:
    setup_logging()
    log = logging.getLogger("test_trade_store_losing_streak")

    raw_cfg = load_config()
    log.info("Config chargée depuis %s", BASE_DIR / "config.json")

    # 1) Build runtime pour récupérer risk_engine + wallet_manager
    config, deps = build_runtime_from_config(raw_cfg)
    risk_engine = deps.risk_engine
    wallet_manager = deps.wallet_manager

    max_ls = risk_engine.config.global_cfg.max_consecutive_losing_trades
    log.info("max_consecutive_losing_trades (config) = %d", max_ls)

    if max_ls <= 0:
        log.warning(
            "max_consecutive_losing_trades <= 0, ce test ne montrera rien. "
            "Mets une valeur > 0 dans config['risk']['global']['max_consecutive_losing_trades']."
        )

    # 2) Crée un TradeStore dédié au test + RiskAwareExecutionEngine associé
    store_logger = logging.getLogger("test_trade_store_losing_streak.TradeStore")
    test_store = TradeStore(logger=store_logger)

    exec_logger = logging.getLogger("test_trade_store_losing_streak.Exec")
    exec_engine = RiskAwareExecutionEngine(
        risk_engine=risk_engine,
        wallet_manager=wallet_manager,
        trade_store=test_store,
        logger_=exec_logger,
    )

    # 3) Simule N trades perdants pour faire monter la losing streak globale
    wallet_id = "sniper_sol"  # doit exister dans ta config
    symbol = "FAKE/USDC"

    n_losses = max_ls if max_ls > 0 else 3  # fallback 3 si max_ls <= 0

    log.info("Simulation de %d trades perdants...", n_losses)

    for i in range(n_losses):
        trade_id = f"loss-{i+1}"
        # On enregistre un trade ouvert puis on le ferme avec PnL négatif
        test_store.register_open_trade(
            trade_id=trade_id,
            wallet_id=wallet_id,
            symbol=symbol,
            side="buy",
            notional_usd=Decimal("100"),
        )
        test_store.close_trade(
            trade_id=trade_id,
            pnl_usd=Decimal("-10"),  # perte de 10 USD
        )

        log.info(
            "Après clôture de %s : losing_streak=%d",
            trade_id,
            test_store.get_global_consecutive_losing_trades(),
        )

    log.info("Snapshot TradeStore après pertes : %s", test_store.debug_snapshot())

    # 4) Envoie un TradeSignal final et regarde la décision du RiskEngine
    sig = TradeSignal(
        id="final-check",
        strategy_id="test_losing_streak",
        wallet_id=wallet_id,
        symbol=symbol,
        side=SignalSide.BUY,
        notional_usd=100.0,
        kind=SignalKind.ENTRY,
        meta={"note": "test losing streak effect"},
    )

    log.info(
        "Envoi d'un TradeSignal final : id=%s wallet=%s symbol=%s side=%s notional=%.2f",
        sig.id,
        sig.wallet_id,
        sig.symbol,
        sig.side.value,
        sig.notional_usd,
    )

    # On wrap un petit hook pour voir directement la décision retournée par evaluate_order
    # tout en passant par RiskAwareExecutionEngine
    # (on pourrait aussi appeler directement risk_engine.evaluate_order, mais autant tester le flux complet).
    # Pour ça, on monkey-patche temporairement logger pour détecter la ligne.
    exec_engine.process_signals([sig], mode=config.execution_mode)

    # On refait explicitement l'appel evaluate_order pour loguer la décision proprement
    ctx = exec_engine._build_order_ctx_from_signal(sig)  # type: ignore[attr-defined]
    decision, size_accepted, reason = risk_engine.evaluate_order(ctx)

    log.info(
        "Décision finale RiskEngine : decision=%s size_accepted=%.2f reason=%s",
        decision.value,
        size_accepted,
        reason,
    )

    if decision is RiskDecision.ADJUST:
        log.info(
            "✅ Comme attendu : losing_streak=%d >= max=%d, taille ajustée.",
            test_store.get_global_consecutive_losing_trades(),
            max_ls,
        )
    elif decision is RiskDecision.REJECT:
        log.info("⚠️ Le moteur rejette l'ordre (REJECT) plutôt qu'ADJUST.")
    elif decision is RiskDecision.EJECT:
        log.info("⚠️ Le moteur est en état EJECT (circuit breaker global).")
    else:
        log.info("ℹ️ Le moteur accepte l'ordre tel quel (ACCEPT).")


if __name__ == "__main__":
    main()
