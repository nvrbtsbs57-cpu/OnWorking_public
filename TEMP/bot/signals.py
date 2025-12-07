from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, getcontext
from enum import Enum
from typing import Dict, List, Optional

# haute précision pour les prix
getcontext().prec = 50


class SignalSide(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


class SignalSource(str, Enum):
    WHALES = "whales_engine"
    LIQUIDITY = "liq_engine"
    ORDER_FLOW = "order_flow_engine"
    OTHER = "other"


@dataclass
class SignalContext:
    """
    Contexte de marché sur lequel le signal s'applique.
    """
    chain: str               # "ethereum", "arbitrum", "base", ...
    market_type: str         # "spot", "perp", ...
    base_token: str          # adresse ou symbole normalisé
    quote_token: str         # ex: "USDC"
    venue: str               # "binance-futures", "uniswap-v3", ...
    symbol: Optional[str]    # ex: "BTCUSDT"


@dataclass
class SignalFeature:
    """
    Brique de signal élémentaire (whale size, liq density, etc.).
    name peut suivre la convention: 'flow_whale_notional', 'trend_slope', ...
    """
    name: str
    value: Decimal
    weight: Decimal = Decimal("1.0")


@dataclass
class RawSignal:
    """
    Signal brut, généré par les modules Order Flow / Whales / Liquidity.
    """
    id: str
    created_at: datetime

    context: SignalContext
    side: SignalSide

    source: str              # ou SignalSource.value
    label: str               # "whale_accumulation", "stop_run", ...

    confidence: Decimal      # 0 à 1
    features: List[SignalFeature] = field(default_factory=list)
    meta: Dict[str, str] = field(default_factory=dict)


@dataclass
class ScoreBreakdown:
    """
    Détail du scoring d’un signal.
    """
    total_score: Decimal
    components: Dict[str, Decimal]    # ex: {"flow": 0.8, "trend": 0.5}


@dataclass
class RiskProfile:
    """
    Profil de risque dérivé du score + volatilité + règles globales.
    """
    risk_level: str                 # "low", "medium", "high"
    max_leverage: Decimal
    max_notional: Decimal
    stop_distance_pct: Decimal      # ex: 0.01 pour 1 %
    take_profit_rr: Decimal         # ex: 3 pour R:R=3


@dataclass
class PositionSize:
    """
    Sizing final proposé pour ce signal.
    """
    account_equity: Decimal
    risk_per_trade_pct: Decimal
    notional: Decimal
    quantity: Decimal
    stop_price: Optional[Decimal]
    take_profit_price: Optional[Decimal]


@dataclass
class ScoredSignal:
    """
    Signal complet, prêt à être consommé par l'AgentEngine / alertes / dashboard.
    """
    raw: RawSignal
    score: ScoreBreakdown
    risk: RiskProfile
    position: Optional[PositionSize]

# ============================================================================
# Compatibilité API / Dashboard : events_to_signals
# ============================================================================

from typing import Iterable, Any


def events_to_signals(events: Optional[Iterable[Any]], *args, **kwargs) -> List[Dict[str, Any]]:
    """
    Fonction de compatibilité utilisée par l'API HTTP.

    Transforme une liste d'events (dict) en une liste de "signaux" simples
    pour le dashboard / endpoints API.

    - On reste volontairement générique ici.
    - Si plus tard on veut exposer les vrais ScoredSignal, on adaptera.
    """
    signals: List[Dict[str, Any]] = []

    if not events:
        return signals

    for ev in events:
        if not isinstance(ev, dict):
            continue

        sig: Dict[str, Any] = {
            "id": ev.get("id")
                or ev.get("tx_hash")
                or ev.get("transaction_hash")
                or ev.get("hash")
                or "",
            "type": ev.get("type") or ev.get("kind") or "event",
            "chain": ev.get("chain"),
            "ts": ev.get("ts") or ev.get("time") or ev.get("timestamp"),
            "payload": ev,
        }

        # quelques champs utiles en plus si présents
        if "symbol" in ev:
            sig["symbol"] = ev["symbol"]
        if "market" in ev:
            sig["market"] = ev["market"]
        if "token" in ev:
            sig["token"] = ev["token"]
        if "notional_usd" in ev:
            sig["notional_usd"] = ev["notional_usd"]

        signals.append(sig)

    return signals

