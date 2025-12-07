from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from bot.signals import (
    SignalContext,
    SignalFeature,
    RawSignal,
    SignalSide,
)
from bot.agent.signals import SignalEngine


# ======================================================================
# Configs de test (scoring + risk) — indépendantes de config.json
# ======================================================================

SCORING_CFG = {
    "min_confidence": 0.4,
    "min_total_score": 0.5,  # <- on baisse un peu pour le test
    "weights": {
        "flow": 0.4,
        "trend": 0.3,
        "liq": 0.2,
        "vol": 0.1,
    },
}

RISK_CFG = {
    "default_risk_per_trade_pct": 0.5,
    "max_risk_per_trade_pct": 1.0,
    "max_global_risk_pct": 5.0,
    "per_market": {
        "ETHUSDT": {
            "risk_per_trade_pct": 0.5,
            "max_leverage": 10,
        }
    },
    "volatility_buckets": [
        {"name": "low", "atr_pct_max": 1.5, "risk_multiplier": 1.2},
        {"name": "medium", "atr_pct_max": 3.0, "risk_multiplier": 1.0},
        {"name": "high", "atr_pct_max": 6.0, "risk_multiplier": 0.6},
    ],
}


# ======================================================================
# Construction d'un RawSignal de démo
# ======================================================================

def build_demo_signal() -> RawSignal:
    """
    Construit un RawSignal fake pour tester le pipeline.
    """

    ctx = SignalContext(
        chain="ethereum",
        market_type="perp",
        base_token="ETH",
        quote_token="USDT",
        venue="binance-futures",
        symbol="ETHUSDT",
    )

    features = [
        # fort flux whale
        SignalFeature(
            name="flow_whale_notional",
            value=Decimal("0.9"),
            weight=Decimal("1.0"),
        ),
        # trend plutôt haussière
        SignalFeature(
            name="trend_slope",
            value=Decimal("0.6"),
            weight=Decimal("1.0"),
        ),
        # bonne liquidité
        SignalFeature(
            name="liq_cluster_density",
            value=Decimal("0.7"),
            weight=Decimal("1.0"),
        ),
    ]

    signal = RawSignal(
        id="demo_1",
        created_at=datetime.utcnow(),
        context=ctx,
        side=SignalSide.LONG,
        source="whales_engine",
        label="demo_whale_long",
        confidence=Decimal("0.8"),
        features=features,
        meta={
            # prix d'entrée utilisé par le RiskEngine pour le sizing
            "entry_price": "2100",
        },
    )

    return signal


# ======================================================================
# Mock des dépendances (wallet / volatilité)
# ======================================================================

def get_account_equity(chain: str) -> Decimal:
    """
    Mock simple: on fait comme si on avait 10k$ d'équity sur chaque chaîne.
    """
    return Decimal("10000")


def get_atr_pct(symbol: str):
    """
    Mock de volatilité: 2% d'ATR, donc plutôt medium.
    """
    return Decimal("2.0")


# ======================================================================
# Main
# ======================================================================

def main():
    # instancie le SignalEngine de l'agent avec nos configs de test
    signal_engine = SignalEngine(
        scoring_cfg=SCORING_CFG,
        risk_cfg=RISK_CFG,
    )

    raw = build_demo_signal()

    scored_list = signal_engine.process_signals(
        signals=[raw],
        get_account_equity=get_account_equity,
        get_atr_pct=get_atr_pct,
    )

    if not scored_list:
        print("❌ Aucun signal accepté par le SignalEngine (score ou confiance trop faibles).")
        return

    scored = scored_list[0]

    print("✅ Signal traité avec succès !\n")
    print("ID: ", scored.raw.id)
    print("Label: ", scored.raw.label)
    print("Side: ", scored.raw.side.value)
    print("--- SCORING ---")
    print("Score total:", scored.score.total_score)
    print("Détail:", scored.score.components)
    print("--- RISK PROFILE ---")
    print("Risk level:", scored.risk.risk_level)
    print("Max leverage:", scored.risk.max_leverage)
    print("Stop distance %:", scored.risk.stop_distance_pct)
    print("RR TP:", scored.risk.take_profit_rr)
    print("--- POSITION ---")
    print("Equity:", scored.position.account_equity)
    print("Risk % par trade:", scored.position.risk_per_trade_pct)
    print("Notional:", scored.position.notional)
    print("Quantity:", scored.position.quantity)
    print("Stop:", scored.position.stop_price)
    print("Take profit:", scored.position.take_profit_price)


if __name__ == "__main__":
    main()
