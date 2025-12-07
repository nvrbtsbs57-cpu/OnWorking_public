from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from bot.trading.models import AgentStatus

DECIMAL_ZERO = Decimal("0")


class WalletSnapshot(BaseModel):
    """Vue d'un wallet logique (base_main, profits_sol, fees, vault, etc.)."""

    wallet_id: str

    balance_usd: Decimal = DECIMAL_ZERO
    realized_pnl_today_usd: Decimal = DECIMAL_ZERO
    gross_pnl_today_usd: Decimal = DECIMAL_ZERO
    fees_paid_today_usd: Decimal = DECIMAL_ZERO

    consecutive_losing_trades: int = 0
    last_reset_date: Optional[date] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class PnLSnapshot(BaseModel):
    """Vue agrégée PnL & drawdown."""

    realized_pnl_today_usd: Decimal = DECIMAL_ZERO
    gross_pnl_today_usd: Decimal = DECIMAL_ZERO
    fees_paid_today_usd: Decimal = DECIMAL_ZERO

    current_drawdown_pct: Optional[Decimal] = None
    max_drawdown_pct: Optional[Decimal] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class TransferStats(BaseModel):
    """Stats d'un type de flux (autofees, sweep, compounding)."""

    planned: int = 0
    executed: int = 0
    failed: int = 0
    last_run_at: Optional[datetime] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class FinancePipelineSnapshot(BaseModel):
    """Vue d’ensemble de la pipeline finance."""

    autofees: Optional[TransferStats] = None
    sweep: Optional[TransferStats] = None
    compounding: Optional[TransferStats] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class FinanceSnapshot(BaseModel):
    """Snapshot finance complet à un instant t."""

    at: datetime

    wallets: Dict[str, WalletSnapshot] = Field(default_factory=dict)
    pnl: PnLSnapshot

    pipeline: Optional[FinancePipelineSnapshot] = None
    runtime_status: Optional[AgentStatus] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class AlertsSummary(BaseModel):
    """Résumé des alertes finance."""

    total: int = 0
    critical: int = 0
    warning: int = 0
    info: int = 0

    last_alert_at: Optional[datetime] = None

    extra: Dict[str, Any] = Field(default_factory=dict)


class OlympusSnapshot(BaseModel):
    """Snapshot global Olympus : finance + alertes."""

    at: datetime
    finance: FinanceSnapshot
    alerts_summary: Optional[AlertsSummary] = None

    extra: Dict[str, Any] = Field(default_factory=dict)
