# bot/alerting/finance_alerts.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

from bot.core.risk import RiskConfig


logger = logging.getLogger("finance_alerts")


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass
class FinanceAlertsConfig:
    """
    Configuration des alertes finance/risk.

    - drawdown_warning_ratio / critical_ratio :
        fraction du max_global_daily_loss_pct à partir
        de laquelle on déclenche une alerte.

      Ex: max_global_daily_loss_pct = 5.0 (%)
          drawdown_warning_ratio  = 0.5  -> alerte WARNING à 2.5% de perte
          drawdown_critical_ratio = 1.0  -> alerte CRITICAL à 5% de perte

    - losing_streak_warning_ratio / critical_ratio :
        fraction de max_consecutive_losing_trades.

    - fees_wallet_name :
        nom du wallet de fees (ex: "fees").

    - fees_warning_buffer_usd / critical_buffer_usd :
        seuils de buffer minimum pour le wallet de fees.
    """

    enabled: bool = True

    drawdown_warning_ratio: Decimal = Decimal("0.5")
    drawdown_critical_ratio: Decimal = Decimal("1.0")

    losing_streak_warning_ratio: Decimal = Decimal("0.5")
    losing_streak_critical_ratio: Decimal = Decimal("1.0")

    fees_wallet_name: str = "fees"
    fees_warning_buffer_usd: Decimal = Decimal("50")
    fees_critical_buffer_usd: Decimal = Decimal("20")

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "FinanceAlertsConfig":
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", True)),
            drawdown_warning_ratio=_to_decimal(
                data.get("drawdown_warning_ratio", "0.5")
            ),
            drawdown_critical_ratio=_to_decimal(
                data.get("drawdown_critical_ratio", "1.0")
            ),
            losing_streak_warning_ratio=_to_decimal(
                data.get("losing_streak_warning_ratio", "0.5")
            ),
            losing_streak_critical_ratio=_to_decimal(
                data.get("losing_streak_critical_ratio", "1.0")
            ),
            fees_wallet_name=str(data.get("fees_wallet_name", "fees")),
            fees_warning_buffer_usd=_to_decimal(
                data.get("fees_warning_buffer_usd", "50")
            ),
            fees_critical_buffer_usd=_to_decimal(
                data.get("fees_critical_buffer_usd", "20")
            ),
        )


def _compute_daily_drawdown_pct(wallet: Mapping[str, Any]) -> Decimal:
    """
    Approximation du drawdown journalier en % pour un wallet :

    - balance_usd : balance actuelle
    - realized_pnl_today_usd : PnL réalisé depuis le début de la journée

    On reconstruit un "equity de début de journée" :
        start_equity = balance_usd - realized_pnl_today_usd

    Puis si la perf est négative :
        dd_pct = -realized_pnl_today_usd / start_equity * 100
    """
    balance = _to_decimal(wallet.get("balance_usd"))
    realized = _to_decimal(wallet.get("realized_pnl_today_usd"))

    start_equity = balance - realized
    if start_equity <= 0:
        return Decimal("0")

    if realized >= 0:
        return Decimal("0")

    dd_pct = (-realized / start_equity) * Decimal("100")
    return dd_pct


def check_finance_alerts(
    wallet_snapshot: Mapping[str, Mapping[str, Any]],
    risk_config: RiskConfig,
    alerts_cfg: FinanceAlertsConfig,
    *,
    log: Optional[logging.Logger] = None,
) -> None:
    """
    Examine un snapshot de wallets et déclenche des alertes via logging.

    Paramètres
    ----------
    wallet_snapshot :
        Dict[str, Dict[str, Any]] tel que retourné par WalletFlowsEngine / WalletManager:
        {
          "base_main": {
            "balance_usd": "1000.00",
            "realized_pnl_today_usd": "-30.00",
            "gross_pnl_today_usd": "-25.00",
            "fees_paid_today_usd": "5.00",
            "consecutive_losing_trades": "3",
            "last_reset_date": "2025-11-26",
          },
          "fees": {...},
          ...
        }

    risk_config :
        Config globale du RiskEngine (pour récupérer
        max_global_daily_loss_pct et max_consecutive_losing_trades).

    alerts_cfg :
        Configuration des seuils d'alertes finance.

    log :
        Logger à utiliser (par défaut "finance_alerts").
    """
    if not alerts_cfg.enabled:
        return

    log = log or logger

    # --- 1) Drawdown global journalier vs max_global_daily_loss_pct ---
    try:
        max_loss_pct = Decimal(str(risk_config.global_cfg.max_global_daily_loss_pct))
    except Exception:
        max_loss_pct = Decimal("0")

    worst_dd_pct = Decimal("0")
    worst_wallet_name = None

    for w_name, w_stats in wallet_snapshot.items():
        dd = _compute_daily_drawdown_pct(w_stats)
        if dd > worst_dd_pct:
            worst_dd_pct = dd
            worst_wallet_name = w_name

    if max_loss_pct > 0 and worst_wallet_name is not None:
        warn_threshold = alerts_cfg.drawdown_warning_ratio * max_loss_pct
        crit_threshold = alerts_cfg.drawdown_critical_ratio * max_loss_pct

        if worst_dd_pct >= crit_threshold:
            log.error(
                "Drawdown journalier CRITIQUE: %.2f%% (wallet=%s, max=%.2f%%).",
                worst_dd_pct,
                worst_wallet_name,
                max_loss_pct,
                extra={
                    "title": "Daily drawdown CRITICAL",
                    "wallet": worst_wallet_name,
                    "drawdown_pct": float(worst_dd_pct),
                    "max_loss_pct": float(max_loss_pct),
                    "alert_key": "finance.drawdown.critical",
                },
            )
        elif worst_dd_pct >= warn_threshold:
            log.warning(
                "Drawdown journalier élevé: %.2f%% (wallet=%s, max=%.2f%%).",
                worst_dd_pct,
                worst_wallet_name,
                max_loss_pct,
                extra={
                    "title": "Daily drawdown warning",
                    "wallet": worst_wallet_name,
                    "drawdown_pct": float(worst_dd_pct),
                    "max_loss_pct": float(max_loss_pct),
                    "alert_key": "finance.drawdown.warning",
                },
            )

    # --- 2) Streak de trades perdants vs max_consecutive_losing_trades ---
    max_streak_cfg = int(risk_config.global_cfg.max_consecutive_losing_trades or 0)
    if max_streak_cfg > 0:
        worst_streak = 0
        worst_wallet_name = None

        for w_name, w_stats in wallet_snapshot.items():
            streak = _to_int(w_stats.get("consecutive_losing_trades"))
            if streak > worst_streak:
                worst_streak = streak
                worst_wallet_name = w_name

        warn_streak = int(alerts_cfg.losing_streak_warning_ratio * max_streak_cfg)
        crit_streak = int(alerts_cfg.losing_streak_critical_ratio * max_streak_cfg)

        if worst_wallet_name is not None and worst_streak > 0:
            if worst_streak >= crit_streak:
                log.error(
                    "Losing streak CRITIQUE: %d trades perdants (wallet=%s, max=%d).",
                    worst_streak,
                    worst_wallet_name,
                    max_streak_cfg,
                    extra={
                        "title": "Losing streak CRITICAL",
                        "wallet": worst_wallet_name,
                        "losing_streak": int(worst_streak),
                        "max_consecutive_losing_trades": max_streak_cfg,
                        "alert_key": "finance.losing_streak.critical",
                    },
                )
            elif worst_streak >= warn_streak:
                log.warning(
                    "Losing streak élevé: %d trades perdants (wallet=%s, max=%d).",
                    worst_streak,
                    worst_wallet_name,
                    max_streak_cfg,
                    extra={
                        "title": "Losing streak warning",
                        "wallet": worst_wallet_name,
                        "losing_streak": int(worst_streak),
                        "max_consecutive_losing_trades": max_streak_cfg,
                        "alert_key": "finance.losing_streak.warning",
                    },
                )

    # --- 3) Buffer du wallet de fees ---
    fees_name = alerts_cfg.fees_wallet_name
    w_fees = wallet_snapshot.get(fees_name)
    if w_fees:
        balance = _to_decimal(w_fees.get("balance_usd"))

        if balance <= alerts_cfg.fees_critical_buffer_usd:
            log.error(
                "Buffer fees CRITIQUE: balance=%s USD (wallet=%s, seuil_critique=%s).",
                balance,
                fees_name,
                alerts_cfg.fees_critical_buffer_usd,
                extra={
                    "title": "Fees wallet buffer CRITICAL",
                    "wallet": fees_name,
                    "balance_usd": float(balance),
                    "warning_buffer_usd": float(alerts_cfg.fees_warning_buffer_usd),
                    "critical_buffer_usd": float(
                        alerts_cfg.fees_critical_buffer_usd
                    ),
                    "alert_key": "finance.fees_buffer.critical",
                },
            )
        elif balance <= alerts_cfg.fees_warning_buffer_usd:
            log.warning(
                "Buffer fees faible: balance=%s USD (wallet=%s, seuil_warning=%s).",
                balance,
                fees_name,
                alerts_cfg.fees_warning_buffer_usd,
                extra={
                    "title": "Fees wallet buffer low",
                    "wallet": fees_name,
                    "balance_usd": float(balance),
                    "warning_buffer_usd": float(alerts_cfg.fees_warning_buffer_usd),
                    "critical_buffer_usd": float(
                        alerts_cfg.fees_critical_buffer_usd
                    ),
                    "alert_key": "finance.fees_buffer.warning",
                },
            )
