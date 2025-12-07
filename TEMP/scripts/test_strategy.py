#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Petit test local du StrategyEngine :

- charge config.json
- construit une StrategyConfig de base
- crée un Signal ETH en mode BUY
- passe par StrategyEngine.build_execution_request(...)
- affiche le résultat

Usage :
    python scripts/test_strategy.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from decimal import Decimal
from dataclasses import asdict

# ======================================================================
# Fix PYTHONPATH pour accéder au package "bot" depuis scripts/
# ======================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ======================================================================
# Imports projet
# ======================================================================

try:
    from bot.config import load_config  # type: ignore
except ImportError:

    def load_config(path: str):
        import json

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


from bot.core.logging import get_logger
from bot.trading.models import Signal, TradeSide
from bot.trading.strategies import StrategyEngine, StrategyConfig

logger = get_logger(__name__)


def build_strategy_config_from_agent(cfg: dict) -> StrategyConfig:
    """
    Construit une StrategyConfig "raisonnable" à partir de la section agent du config.json.
    On ne dépend pas de méthodes spécifiques, on modifie juste les champs de base.
    """
    sc = StrategyConfig()

    # Taille par défaut : agent.trade_default_notional_usd
    try:
        default_notional = cfg.get("trade_default_notional_usd")
        if default_notional is not None:
            sc.default_notional_usd = Decimal(str(default_notional))
    except Exception:
        pass

    # min_confidence : agent.signal_engine.min_confidence
    try:
        se_cfg = cfg.get("signal_engine") or {}
        min_conf = se_cfg.get("min_confidence")
        if min_conf is not None:
            sc.min_confidence = float(min_conf)
    except Exception:
        pass

    # Cap global de taille (optionnel, on fixe une valeur safe par défaut)
    sc.max_notional_usd = Decimal("5000")

    return sc


def main() -> None:
    cfg_path = BASE_DIR / "config.json"
    print(f"[test_strategy] Chargement de la config depuis {cfg_path}")

    try:
        cfg = load_config(str(cfg_path))
    except Exception as e:
        print(f"[test_strategy] ERREUR: impossible de charger la config: {e}")
        return

    # On récupère la section "agent" (dict)
    if isinstance(cfg, dict):
        agent_cfg = cfg.get("agent", {})
    else:
        agent_cfg = getattr(cfg, "agent", {})

    if not isinstance(agent_cfg, dict):
        agent_cfg = {}

    # ------------------------------------------------------------------
    # StrategyEngine
    # ------------------------------------------------------------------
    strategy_config = build_strategy_config_from_agent(agent_cfg)
    strategy_engine = StrategyEngine(strategy_config)

    print("\n[test_strategy] StrategyConfig utilisée :")
    print(f"  - default_notional_usd = {strategy_config.default_notional_usd}")
    print(f"  - min_confidence       = {strategy_config.min_confidence}")
    print(f"  - max_notional_usd     = {strategy_config.max_notional_usd}")

    # ------------------------------------------------------------------
    # Construction d'un Signal de test
    # ------------------------------------------------------------------
    test_signal = Signal(
        chain="ethereum",
        symbol="ETH",               # simple, lisible
        side=TradeSide.BUY,         # on teste un BUY
        size_usd=Decimal("0"),      # 0 => la stratégie utilisera default_notional_usd
        strategy_id="test_strategy_signal",
        confidence=0.8,             # > min_confidence => devrait passer
        meta={
            "note": "test depuis test_strategy.py",
            # tu peux tester le routing wallet plus tard avec :
            # "wallet_role": "MAIN",
            # "wallet_tags": ["main", "eth"],
        },
    )

    print("\n[test_strategy] Signal d'entrée :")
    print(test_signal.to_dict())

    # ------------------------------------------------------------------
    # Passage par StrategyEngine
    # ------------------------------------------------------------------
    exec_req = strategy_engine.build_execution_request(test_signal)

    if exec_req is None:
        print("\n[test_strategy] 🛑 Signal REJETÉ par la stratégie.")
    else:
        print("\n[test_strategy] ✅ Signal ACCEPTÉ, ExecutionRequest générée :")
        try:
            print(asdict(exec_req))
        except Exception:
            print(exec_req)

    print("\n[test_strategy] Terminé.")


if __name__ == "__main__":
    main()
