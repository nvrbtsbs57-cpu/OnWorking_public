from __future__ import annotations

from pathlib import Path
from datetime import datetime
from decimal import Decimal
import sys
from dataclasses import asdict, is_dataclass

# --- Préparer le PYTHONPATH comme dans start_bot.py ---

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# --- Imports projet ---

from bot.config import load_config  # type: ignore
from bot.agent.signals import SignalEngine as AgentSignalEngine  # type: ignore
from bot.signals import (  # type: ignore
    SignalContext,
    SignalFeature,
    RawSignal,
    SignalSide,
)


def main() -> None:
    # 1) Charger la config globale (comme start_bot)
    config_path = ROOT / "config.json"
    cfg = load_config(str(config_path))

    # On convertit en dict si c'est une dataclass (BotConfig)
    if is_dataclass(cfg):
        cfg_dict = asdict(cfg)
    elif isinstance(cfg, dict):
        cfg_dict = cfg
    else:
        raise TypeError(f"Config retournée par load_config de type inattendu: {type(cfg)}")

    agent_cfg = cfg_dict.get("agent", {}) or {}
    scoring_cfg = agent_cfg.get("signal_engine", {}) or {}
    risk_cfg = agent_cfg.get("risk", {}) or {}

    print("=== Config signal_engine (depuis config.json) ===")
    print(scoring_cfg)
    print("================================================\n")

    # 2) On construit une version "debug" hyper permissive pour le test
    scoring_cfg_test = dict(scoring_cfg)  # copie
    # Si pas de poids, on met des poids simples
    if "weights" not in scoring_cfg_test or not scoring_cfg_test["weights"]:
        scoring_cfg_test["weights"] = {
            "flow": 1.0,
            "trend": 1.0,
            "liq": 1.0,
            "vol": 1.0,
        }

    # On désactive les filtres pour ce test (0 = tout passe)
    scoring_cfg_test["min_confidence"] = 0.0
    scoring_cfg_test["min_total_score"] = 0.0

    print("=== Config signal_engine utilisée pour le TEST ===")
    print(scoring_cfg_test)
    print("=================================================\n")

    # 3) Initialiser le SignalEngine agent avec la config de TEST
    engine = AgentSignalEngine(
        scoring_cfg=scoring_cfg_test,
        risk_cfg=risk_cfg,
    )

    # 4) Construire un faux contexte de signal (ex: whale PEPE/ETH sur ethereum)
    ctx = SignalContext(
        chain="ethereum",
        market_type="spot",
        base_token="PEPE",
        quote_token="ETH",
        venue="uniswap_v2",
        symbol="PEPE/ETH",
    )

    # Features bien “bullish”
    features = [
        SignalFeature(name="flow_whale_notional", value=Decimal("1.0"), weight=Decimal("1.0")),
        SignalFeature(name="trend_short_term",    value=Decimal("0.8"), weight=Decimal("1.0")),
        SignalFeature(name="liq_depth",           value=Decimal("0.7"), weight=Decimal("1.0")),
        SignalFeature(name="vol_spike",           value=Decimal("0.6"), weight=Decimal("1.0")),
    ]

    raw = RawSignal(
        id="test_whale_1",
        created_at=datetime.utcnow(),
        context=ctx,
        side=SignalSide.LONG,
        source="unit_test",
        label="test_whale_buy",
        confidence=Decimal("0.8"),
        features=features,
        meta={
            "entry_price": "0.000001",
        },
    )

    # 5) Stubs pour equity et ATR
    def get_account_equity(chain: str) -> Decimal:
        # Exemple : on simule 10 000 $ d'equity sur la chain
        return Decimal("10000")

    def get_atr_pct(symbol: str) -> Decimal:
        # Exemple : 2 % d'ATR
        return Decimal("2")

    scored = engine.process_signals(
        signals=[raw],
        get_account_equity=get_account_equity,
        get_atr_pct=get_atr_pct,
    )

    print("=== Scored signals ===")
    if not scored:
        print("Aucun signal n'a passé le filtre de scoring (même en mode TEST).")
        return

    for s in scored:
        print("symbol   :", s.raw.context.symbol)
        print("side     :", s.raw.side)
        # suivant comment ScoreBreakdown est défini
        score_val = getattr(getattr(s, "score", None), "total_score", "N/A")
        print("score    :", score_val)
        if s.risk is not None:
            print("risk lvl :", getattr(s.risk, "risk_level", "N/A"))
            print("risk pct :", getattr(s.risk, "risk_per_trade_pct", "N/A"), "%")
        else:
            print("risk     : None")
        if s.position is not None:
            print("notional :", getattr(s.position, "notional", "N/A"))
        else:
            print("position : None")
        print("-" * 40)


if __name__ == "__main__":
    main()
