# bot/alerting/finance.py

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Literal

from bot.olympus.models import FinanceSnapshot  # déjà existant dans ton plan
from bot.trading.models import AgentStatus      # tu l'utilises déjà pour /godmode/status


Severity = Literal["info", "warning", "critical"]
Scope = Literal["global", "wallet"]


@dataclass
class FinanceAlert:
    """
    Alerte finance “pure” pour Olympus / /alerts/recent.

    - id        : identifiant stable (code + scope)
    - at        : datetime UTC
    - severity  : info / warning / critical
    - code      : code machine (FIN_XXX)
    - message   : message humain
    - scope     : global ou wallet
    - wallet_id : wallet concerné (si scope=wallet)
    - meta      : détails (chiffres, seuils, …)
    """
    id: str
    at: datetime
    severity: Severity
    code: str
    message: str
    scope: Scope = "global"
    wallet_id: Optional[str] = None
    meta: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # sérialisation propre pour JSON
        data["at"] = self.at.isoformat()
        return data


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _get_alerts_finance_config(raw_config: Mapping[str, Any]) -> Dict[str, Any]:
    alerts = raw_config.get("alerts", {}) or {}
    return alerts.get("finance", {}) or {}


def _get_global_risk_config(raw_config: Mapping[str, Any]) -> Dict[str, Any]:
    risk = raw_config.get("risk", {}) or {}
    return risk.get("global", {}) or {}


def _get_wallet_roles(raw_config: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    return raw_config.get("wallet_roles", {}) or {}


def _severity_order(severity: Severity) -> int:
    if severity == "info":
        return 0
    if severity == "warning":
        return 1
    return 2  # critical


# ---------------------------------------------------------------------------
#  Rules helpers
# ---------------------------------------------------------------------------

def _rule_fees_wallet_low(
    alerts: List[FinanceAlert],
    *,
    now: datetime,
    finance_snapshot: FinanceSnapshot,
    raw_config: Mapping[str, Any],
) -> None:
    cfg_fin = _get_alerts_finance_config(raw_config)
    wallets = finance_snapshot.wallets or {}

    fees_wallet_name = cfg_fin.get("fees_wallet_name", "fees")
    fees_warning_buffer_usd = _decimal(cfg_fin.get("fees_warning_buffer_usd", 50))
    fees_critical_buffer_usd = _decimal(cfg_fin.get("fees_critical_buffer_usd", 20))

    fees_wallet = wallets.get(fees_wallet_name)
    if fees_wallet is None:
        return

    balance = _decimal(getattr(fees_wallet, "balance_usd", None))
    # volume = somme des |gross_pnl_today_usd|
    total_volume_today = sum(
        abs(_decimal(getattr(w, "gross_pnl_today_usd", None)))
        for w in wallets.values()
    )

    severity: Optional[Severity] = None
    code = ""
    message = ""

    if balance <= Decimal("0") and total_volume_today > Decimal("0"):
        severity = "critical"
        code = "FIN_FEES_ZERO_WITH_VOLUME"
        message = (
            f"Wallet fees '{fees_wallet_name}' est à 0 alors qu'il y a du volume "
            f"aujourd'hui (~{total_volume_today} USD)."
        )
    elif balance <= fees_critical_buffer_usd:
        severity = "critical"
        code = "FIN_FEES_LOW_CRITICAL"
        message = (
            f"Wallet fees '{fees_wallet_name}' très bas : "
            f"{balance} USD ≤ seuil critique {fees_critical_buffer_usd} USD."
        )
    elif balance <= fees_warning_buffer_usd:
        severity = "warning"
        code = "FIN_FEES_LOW_WARNING"
        message = (
            f"Wallet fees '{fees_wallet_name}' bas : "
            f"{balance} USD ≤ seuil warning {fees_warning_buffer_usd} USD."
        )

    if severity is not None:
        alerts.append(
            FinanceAlert(
                id=code,
                at=now,
                severity=severity,
                code=code,
                message=message,
                scope="wallet",
                wallet_id=fees_wallet_name,
                meta={
                    "balance_usd": str(balance),
                    "warning_buffer_usd": str(fees_warning_buffer_usd),
                    "critical_buffer_usd": str(fees_critical_buffer_usd),
                    "total_volume_today_usd": str(total_volume_today),
                },
            )
        )


def _rule_drawdown(
    alerts: List[FinanceAlert],
    *,
    now: datetime,
    finance_snapshot: FinanceSnapshot,
    raw_config: Mapping[str, Any],
) -> None:
    cfg_fin = _get_alerts_finance_config(raw_config)
    cfg_risk = _get_global_risk_config(raw_config)

    pnl = finance_snapshot.pnl
    if pnl is None:
        return

    current_dd_pct = _decimal(getattr(pnl, "current_drawdown_pct", None))
    if current_dd_pct <= Decimal("0"):
        return

    max_global_daily_loss_pct = _decimal(
        cfg_risk.get("max_global_daily_loss_pct", 5.0)
    )
    drawdown_warning_ratio = _decimal(cfg_fin.get("drawdown_warning_ratio", 0.5))
    drawdown_critical_ratio = _decimal(cfg_fin.get("drawdown_critical_ratio", 1.0))

    warning_threshold_pct = drawdown_warning_ratio * max_global_daily_loss_pct
    critical_threshold_pct = drawdown_critical_ratio * max_global_daily_loss_pct

    severity: Optional[Severity] = None
    code = ""
    message = ""

    if current_dd_pct >= critical_threshold_pct:
        severity = "critical"
        code = "FIN_DRAWDOWN_CRITICAL"
        message = (
            f"Drawdown courant {current_dd_pct:.2f}% ≥ seuil critique "
            f"{critical_threshold_pct:.2f}% (limite globale {max_global_daily_loss_pct:.2f}%)."
        )
    elif current_dd_pct >= warning_threshold_pct:
        severity = "warning"
        code = "FIN_DRAWDOWN_WARNING"
        message = (
            f"Drawdown courant {current_dd_pct:.2f}% ≥ seuil warning "
            f"{warning_threshold_pct:.2f}% (limite globale {max_global_daily_loss_pct:.2f}%)."
        )

    if severity is not None:
        alerts.append(
            FinanceAlert(
                id=code,
                at=now,
                severity=severity,
                code=code,
                message=message,
                scope="global",
                wallet_id=None,
                meta={
                    "current_drawdown_pct": float(current_dd_pct),
                    "warning_threshold_pct": float(warning_threshold_pct),
                    "critical_threshold_pct": float(critical_threshold_pct),
                    "max_global_daily_loss_pct": float(max_global_daily_loss_pct),
                },
            )
        )


def _rule_losing_streak(
    alerts: List[FinanceAlert],
    *,
    now: datetime,
    finance_snapshot: FinanceSnapshot,
    raw_config: Mapping[str, Any],
) -> None:
    cfg_fin = _get_alerts_finance_config(raw_config)
    cfg_risk = _get_global_risk_config(raw_config)
    wallets = finance_snapshot.wallets or {}

    max_consecutive_losing_trades = int(
        cfg_risk.get("max_consecutive_losing_trades", 0)
    )
    if max_consecutive_losing_trades <= 0:
        return

    losing_streak_warning_ratio = _decimal(
        cfg_fin.get("losing_streak_warning_ratio", 0.5)
    )
    losing_streak_critical_ratio = _decimal(
        cfg_fin.get("losing_streak_critical_ratio", 1.0)
    )

    warning_threshold = int(
        (losing_streak_warning_ratio * max_consecutive_losing_trades).to_integral_value(rounding="ROUND_CEILING")
    )
    critical_threshold = int(
        (losing_streak_critical_ratio * max_consecutive_losing_trades).to_integral_value(rounding="ROUND_CEILING")
    )

    # on cherche le wallet avec la plus grosse losing streak
    worst_wallet = None
    worst_streak = 0

    for w in wallets.values():
        streak = int(getattr(w, "consecutive_losing_trades", 0) or 0)
        if streak > worst_streak:
            worst_streak = streak
            worst_wallet = w

    if worst_wallet is None or worst_streak <= 0:
        return

    severity: Optional[Severity] = None
    code = ""
    message = ""

    if worst_streak >= critical_threshold:
        severity = "critical"
        code = "FIN_LOSING_STREAK_CRITICAL"
        message = (
            f"Losing streak de {worst_streak} trades sur le wallet "
            f"'{worst_wallet.wallet_id}' ≥ seuil critique {critical_threshold} "
            f"(limite globale {max_consecutive_losing_trades})."
        )
    elif worst_streak >= warning_threshold:
        severity = "warning"
        code = "FIN_LOSING_STREAK_WARNING"
        message = (
            f"Losing streak de {worst_streak} trades sur le wallet "
            f"'{worst_wallet.wallet_id}' ≥ seuil warning {warning_threshold} "
            f"(limite globale {max_consecutive_losing_trades})."
        )

    if severity is not None:
        alerts.append(
            FinanceAlert(
                id=f"{code}:{worst_wallet.wallet_id}",
                at=now,
                severity=severity,
                code=code,
                message=message,
                scope="wallet",
                wallet_id=worst_wallet.wallet_id,
                meta={
                    "streak": worst_streak,
                    "warning_threshold": warning_threshold,
                    "critical_threshold": critical_threshold,
                    "max_consecutive_losing_trades": max_consecutive_losing_trades,
                },
            )
        )


def _rule_profits_sweep_overdue(
    alerts: List[FinanceAlert],
    *,
    now: datetime,
    finance_snapshot: FinanceSnapshot,
    raw_config: Mapping[str, Any],
) -> None:
    wallets = finance_snapshot.wallets or {}
    wallet_roles = _get_wallet_roles(raw_config)
    profits_roles = wallet_roles.get("profits", {}) or {}

    # si pas de roles déclarés, on prend tous les wallets qui commencent par "profits_"
    profit_wallet_ids = set(profits_roles.values())
    if not profit_wallet_ids:
        profit_wallet_ids = {wid for wid in wallets.keys() if wid.startswith("profits_")}

    profit_wallets = [wallets[wid] for wid in profit_wallet_ids if wid in wallets]

    if not profit_wallets:
        return

    finance_cfg = raw_config.get("finance", {}) or {}
    sweep_cfg = finance_cfg.get("sweep", {}) or {}
    min_profit_usd = _decimal(sweep_cfg.get("min_profit_usd", "50"))

    total_profits_balance = sum(
        _decimal(getattr(w, "balance_usd", None)) for w in profit_wallets
    )

    # règle simple v1 : warning si ≥ 2 * min_profit, critical si ≥ 3 * min_profit
    warning_threshold = min_profit_usd * Decimal("2")
    critical_threshold = min_profit_usd * Decimal("3")

    severity: Optional[Severity] = None
    code = ""
    message = ""

    if total_profits_balance >= critical_threshold:
        severity = "critical"
        code = "FIN_PROFITS_SWEEP_CRITICAL"
        message = (
            f"Profits cumulés {total_profits_balance} USD ≥ {critical_threshold} USD "
            f"(3× min_profit_usd={min_profit_usd}) : sweep vers 'vault' très en retard."
        )
    elif total_profits_balance >= warning_threshold:
        severity = "warning"
        code = "FIN_PROFITS_SWEEP_WARNING"
        message = (
            f"Profits cumulés {total_profits_balance} USD ≥ {warning_threshold} USD "
            f"(2× min_profit_usd={min_profit_usd}) : sweep vers 'vault' en retard."
        )

    if severity is not None:
        alerts.append(
            FinanceAlert(
                id=code,
                at=now,
                severity=severity,
                code=code,
                message=message,
                scope="global",
                wallet_id=None,
                meta={
                    "total_profits_balance_usd": str(total_profits_balance),
                    "min_profit_usd": str(min_profit_usd),
                    "warning_threshold_usd": str(warning_threshold),
                    "critical_threshold_usd": str(critical_threshold),
                    "profit_wallet_ids": sorted(profit_wallet_ids),
                },
            )
        )


def _rule_balances_incoherent(
    alerts: List[FinanceAlert],
    *,
    now: datetime,
    finance_snapshot: FinanceSnapshot,
    raw_config: Mapping[str, Any],
) -> None:
    wallets = finance_snapshot.wallets or {}
    pnl = finance_snapshot.pnl

    # 1) wallet négatif -> CRITICAL
    for w in wallets.values():
        balance = _decimal(getattr(w, "balance_usd", None))
        if balance < Decimal("0"):
            code = "FIN_WALLET_NEGATIVE_BALANCE"
            message = (
                f"Balance négative détectée sur le wallet '{w.wallet_id}': {balance} USD."
            )
            alerts.append(
                FinanceAlert(
                    id=f"{code}:{w.wallet_id}",
                    at=now,
                    severity="critical",
                    code=code,
                    message=message,
                    scope="wallet",
                    wallet_id=w.wallet_id,
                    meta={"balance_usd": str(balance)},
                )
            )

    # 2) profits_* qui ne bougent pas alors que le PnL réalisé est significatif
    finance_cfg = raw_config.get("finance", {}) or {}
    sweep_cfg = finance_cfg.get("sweep", {}) or {}
    min_profit_usd = _decimal(sweep_cfg.get("min_profit_usd", "50"))

    wallet_roles = _get_wallet_roles(raw_config)
    profits_roles = wallet_roles.get("profits", {}) or {}
    profit_wallet_ids = set(profits_roles.values())
    if not profit_wallet_ids:
        profit_wallet_ids = {wid for wid in wallets.keys() if wid.startswith("profits_")}

    profit_wallets = [wallets[wid] for wid in profit_wallet_ids if wid in wallets]

    if pnl is not None and profit_wallets:
        realized_today = _decimal(getattr(pnl, "realized_pnl_today_usd", None))
        total_profits_balance = sum(
            _decimal(getattr(w, "balance_usd", None)) for w in profit_wallets
        )

        # règle v1 : si realized_pnl_today > min_profit_usd et profits_* == 0 => warning
        if realized_today > min_profit_usd and total_profits_balance <= Decimal("0"):
            code = "FIN_PROFITS_NOT_MOVING"
            message = (
                f"Pnl réalisé aujourd'hui {realized_today} USD > min_profit_usd={min_profit_usd}, "
                f"mais les wallets de profits {sorted(profit_wallet_ids)} sont toujours à 0."
            )
            alerts.append(
                FinanceAlert(
                    id=code,
                    at=now,
                    severity="warning",
                    code=code,
                    message=message,
                    scope="global",
                    wallet_id=None,
                    meta={
                        "realized_pnl_today_usd": str(realized_today),
                        "min_profit_usd": str(min_profit_usd),
                        "profit_wallet_ids": sorted(profit_wallet_ids),
                    },
                )
            )


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def build_finance_alerts(
    finance_snapshot: FinanceSnapshot,
    raw_config: Optional[Mapping[str, Any]] = None,
    agent_status: Optional[AgentStatus] = None,
) -> List[FinanceAlert]:
    """
    Génère les alertes finance à partir d'un FinanceSnapshot.

    - finance_snapshot : snapshot complet Olympus finance (M9.1).
    - raw_config       : config globale (config.json brut, utile pour les seuils).
    - agent_status     : optionnel pour des règles plus tard (kill-switch runtime, etc.).

    La fonction respecte les règles M9.3 :
      - Fees wallet bas,
      - Vault / profits sweep en retard,
      - Drawdown,
      - Incohérence balances,
      - Losing streak (via risk.global.max_consecutive_losing_trades).
    """
    if raw_config is None:
        raw_config = {}

    now = finance_snapshot.at or datetime.utcnow()
    alerts: List[FinanceAlert] = []

    # Règles principales
    _rule_fees_wallet_low(alerts, now=now, finance_snapshot=finance_snapshot, raw_config=raw_config)
    _rule_drawdown(alerts, now=now, finance_snapshot=finance_snapshot, raw_config=raw_config)
    _rule_losing_streak(alerts, now=now, finance_snapshot=finance_snapshot, raw_config=raw_config)
    _rule_profits_sweep_overdue(alerts, now=now, finance_snapshot=finance_snapshot, raw_config=raw_config)
    _rule_balances_incoherent(alerts, now=now, finance_snapshot=finance_snapshot, raw_config=raw_config)

    # Filtrage par min_severity_for_alert (config.alerts)
    alerts_cfg = raw_config.get("alerts", {}) or {}
    min_sev_str = str(alerts_cfg.get("min_severity_for_alert", "info")).lower()
    min_sev: Severity = "info"
    if min_sev_str in ("warning", "warn"):
        min_sev = "warning"
    elif min_sev_str in ("critical", "crit"):
        min_sev = "critical"

    min_level = _severity_order(min_sev)
    filtered = [a for a in alerts if _severity_order(a.severity) >= min_level]

    # NOTE: agent_status est là pour M10 (kill-switch quand trop de CRITICAL, etc.)
    # Pour l'instant on ne s'en sert pas, mais la signature est prête.

    return filtered


def summarize_finance_alerts(alerts: Sequence[FinanceAlert]) -> Dict[str, Any]:
    """
    Petit résumé multi-severité, pour remplir AlertsSummary ou pour l'API JSON.
    """
    counts = {"info": 0, "warning": 0, "critical": 0}
    for a in alerts:
        counts[a.severity] = counts.get(a.severity, 0) + 1

    # on prend les 10 dernières alertes pour le résumé
    last_alerts = [a.to_dict() for a in list(alerts)[-10:]]

    return {
        "total": len(alerts),
        "by_severity": counts,
        "has_critical": counts.get("critical", 0) > 0,
        "last_alerts": last_alerts,
    }
