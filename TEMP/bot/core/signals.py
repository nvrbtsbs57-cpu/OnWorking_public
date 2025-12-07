# bot/core/signals.py

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict

from bot.core.risk import RiskDecision


class SignalSide(str, Enum):
    """
    Sens du trade.
    """
    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


class SignalKind(str, Enum):
    """
    Type de signal : entrée, sortie, TP, SL...
    """
    ENTRY = "entry"
    EXIT = "exit"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"


@dataclass
class TradeSignal:
    """
    Signal standardisé produit par les stratégies.

    C'est ce format qu'on fera circuler dans le runtime :
    - StrategyEngine.next_signals() -> List[TradeSignal]
    - RiskEngine.apply_global_limits(...) reçoit des TradeSignal
    - ExecutionEngine.process_signals(...) consomme ces TradeSignal
    """
    id: str
    strategy_id: str
    wallet_id: str
    symbol: str                 # ex: "SOL/USDC" ou "SOLUSDT"
    side: SignalSide
    notional_usd: float
    kind: SignalKind = SignalKind.ENTRY
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderIntent:
    """
    Intent d'ordre après passage par le RiskEngine.

    - signal : le TradeSignal original
    - approved_notional_usd : taille finalisée (après risk)
    - risk_decision : ACCEPT / ADJUST / REJECT / EJECT
    - risk_reason : pourquoi
    """
    signal: TradeSignal
    approved_notional_usd: float
    risk_decision: RiskDecision
    risk_reason: str = ""
