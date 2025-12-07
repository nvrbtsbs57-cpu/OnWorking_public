from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Mapping, Optional

from bot.olympus.models import (
    WalletSnapshot,
    PnLSnapshot,
    FinanceSnapshot,
    FinancePipelineSnapshot,
    AlertsSummary,
    OlympusSnapshot,
)
from bot.trading.models import AgentStatus

DECIMAL_ZERO = Decimal("0")


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return DECIMAL_ZERO
    try:
        return Decimal(str(value))
    except Exception:
        return DECIMAL_ZERO


def _build_wallet_snapshot(wallet_id: str, raw: Mapping[str, Any]) -> WalletSnapshot:
    lr = raw.get("last_reset_date")
    last_reset: Optional[date] = None

    if isinstance(lr, date):
        last_reset = lr
    elif isinstance(lr, str):
        try:
            y, m, d = [int(x) for x in lr.split("-")]
            last_reset = date(y, m, d)
        except Exception:
            last_reset = None

    try:
        streak = int(raw.get("consecutive_losing_trades", 0))
    except Exception:
        streak = 0

    known = {
        "balance_usd",
        "realized_pnl_today_usd",
        "gross_pnl_today_usd",
        "fees_paid_today_usd",
        "consecutive_losing_trades",
        "last_reset_date",
    }
    extra: Dict[str, Any] = {k: v for k, v in raw.items() if k not in known}

    return WalletSnapshot(
        wallet_id=wallet_id,
        balance_usd=_to_decimal(raw.get("balance_usd")),
        realized_pnl_today_usd=_to_decimal(raw.get("realized_pnl_today_usd")),
        gross_pnl_today_usd=_to_decimal(raw.get("gross_pnl_today_usd")),
        fees_paid_today_usd=_to_decimal(raw.get("fees_paid_today_usd")),
        consecutive_losing_trades=streak,
        last_reset_date=last_reset,
        extra=extra,
    )


def _get_wallets_dict(engine: Any) -> Mapping[str, Mapping[str, Any]]:
    # 1) attribut direct .wallets
    wallets_attr = getattr(engine, "wallets", None)
    if isinstance(wallets_attr, dict):
        return wallets_attr

    # 2) méthodes candidates
    for name in (
        "snapshot",
        "debug_snapshot",
        "get_snapshot",
        "get_wallets_snapshot",
        "snapshot_wallets",
        "wallets_snapshot",
        "to_dict",
    ):
        m = getattr(engine, name, None)
        if callable(m):
            res = m()
            if isinstance(res, dict):
                return res

    raise TypeError(
        "WalletFlowsEngine: aucune méthode snapshot trouvée "
        "(essayé: wallets, snapshot, debug_snapshot, "
        "get_snapshot, get_wallets_snapshot, snapshot_wallets, "
        "wallets_snapshot, to_dict)."
    )


def _aggregate_pnl(wallets: Dict[str, WalletSnapshot]) -> PnLSnapshot:
    realized = DECIMAL_ZERO
    gross = DECIMAL_ZERO
    fees = DECIMAL_ZERO

    for w in wallets.values():
        realized += w.realized_pnl_today_usd
        gross += w.gross_pnl_today_usd
        fees += w.fees_paid_today_usd

    return PnLSnapshot(
        realized_pnl_today_usd=realized,
        gross_pnl_today_usd=gross,
        fees_paid_today_usd=fees,
    )


def build_finance_snapshot_from_wallet_engine(
    wallet_engine: Any,
    *,
    runtime_status: Optional[AgentStatus] = None,
    pipeline_snapshot: Optional[FinancePipelineSnapshot] = None,
    now: Optional[datetime] = None,
) -> FinanceSnapshot:
    if now is None:
        now = datetime.utcnow()

    raw = _get_wallets_dict(wallet_engine)

    wallets: Dict[str, WalletSnapshot] = {}
    for wid, data in raw.items():
        if isinstance(data, dict):
            wallets[wid] = _build_wallet_snapshot(wid, data)

    pnl = _aggregate_pnl(wallets)

    return FinanceSnapshot(
        at=now,
        wallets=wallets,
        pnl=pnl,
        pipeline=pipeline_snapshot,
        runtime_status=runtime_status,
    )


def build_olympus_snapshot(
    finance_snapshot: FinanceSnapshot,
    alerts_summary: Optional[AlertsSummary] = None,
    *,
    now: Optional[datetime] = None,
) -> OlympusSnapshot:
    if now is None:
        now = datetime.utcnow()

    return OlympusSnapshot(
        at=now,
        finance=finance_snapshot,
        alerts_summary=alerts_summary,
    )
