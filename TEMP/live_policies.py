from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


# ------------------------------------------------------------
# Constantes de zones FEES
# ------------------------------------------------------------

FEES_UNDER_HARD_BUFFER = "FEES_UNDER_HARD_BUFFER"
FEES_UNDER_BUFFER = "FEES_UNDER_BUFFER"
FEES_SAFE = "FEES_SAFE"
FEES_OVER_CAP = "FEES_OVER_CAP"


@dataclass
class FeesState:
    wallet_id: str
    balance_usd: float
    hard_buffer_usd: float
    soft_buffer_usd: float
    dynamic_cap_usd: float
    zone: str
    violations: List[str]
    # Champs "v2" pour préparer la logique de sweeps / monitoring
    target_pct: float = 0.0
    target_fees_usd: float = 0.0
    surplus_usd: float = 0.0
    would_sweep: bool = False
    sweep_min_usd: float = 0.0
    profits_share_pct: float = 0.0
    vault_share_pct: float = 0.0
    cooldown_minutes: int = 0
    last_sweep_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _to_decimal(x: Any, default: str = "0") -> Decimal:
    if x is None:
        return Decimal(default)
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal(default)


def _parse_datetime_utc(value: Any) -> Optional[datetime]:
    """
    Parse une valeur potentiellement datetime / str ISO en datetime UTC.

    - None -> None
    - datetime naive -> supposée UTC
    - datetime avec tzinfo -> convertie en UTC
    - str -> tentative de fromisoformat (supporte "...Z" -> UTC)
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    try:
        txt = str(value).strip()
        if not txt:
            return None

        # Gestion simple du suffixe 'Z'
        if txt.endswith("Z"):
            txt = txt[:-1] + "+00:00"

        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------
# FEES STATE (10.2.2 / M10 – v2 avec préparation sweeps)
# ------------------------------------------------------------

def compute_fees_state(
    *,
    wallet_id: str,
    wallet_balance_usd: Any,
    total_equity_usd: Any,
    hard_buffer_usd: Any,
    soft_buffer_usd: Any,
    max_fees_usd: Any,
    max_fees_equity_pct: Any,
    # Paramètres optionnels pour la logique de sweeps (préparée pour M10+)
    target_pct: Any = None,
    sweep_min_usd: Any = None,
    profits_share_pct: Any = None,
    vault_share_pct: Any = None,
    cooldown_minutes: Optional[int] = None,
    last_sweep_at: Any = None,
) -> Dict[str, Any]:
    """
    Calcule la zone du wallet fees + violations + infos de surplus/sweep.

    Typiquement câblé avec config.json :

      alerts_finance = cfg["alerts"]["finance"]
      hard_buffer_usd = alerts_finance["fees_critical_buffer_usd"]
      soft_buffer_usd = alerts_finance["fees_warning_buffer_usd"]

      fees_cfg = cfg["finance"]["policies"]["fees"]
      max_fees_usd = fees_cfg["max_usd"]

      fees_policy = cfg["finance"]["fees_policy"]
      max_fees_equity_pct = fees_policy["max_equity_pct"]

    Et les données runtime :

      - wallet_balance_usd     -> wallets_runtime.json (wallet 'fees')
      - total_equity_usd       -> wallets_runtime.json["equity_total_usd"]

    Pour la partie sweeps (optionnelle, M10+) :

      sweeps_cfg = cfg["finance"]["policies"]["fees_sweeps"]
      target_pct        = sweeps_cfg["target_pct"]
      sweep_min_usd     = sweeps_cfg["sweep_min_usd"]
      profits_share_pct = sweeps_cfg["profits_share_pct"]
      vault_share_pct   = sweeps_cfg["vault_share_pct"]
      cooldown_minutes  = sweeps_cfg["cooldown_minutes"]

      last_sweep_at     -> timestamp ISO de la dernière opération de sweep (ou None)
    """

    balance = _to_decimal(wallet_balance_usd)
    total_equity = _to_decimal(total_equity_usd)
    hard_buffer = _to_decimal(hard_buffer_usd)
    soft_buffer = _to_decimal(soft_buffer_usd)
    max_usd = _to_decimal(max_fees_usd)
    max_eq_pct = _to_decimal(max_fees_equity_pct)

    # cap_dynamique = min(max_usd, equity_total * max_equity_pct)
    if total_equity <= 0 or max_eq_pct <= 0:
        dynamic_cap = max_usd
    else:
        dynamic_cap = min(max_usd, total_equity * max_eq_pct)

    # ----------------------------------------------------------------
    # Zones FEES (comportement identique à la v1)
    # ----------------------------------------------------------------
    zone = FEES_SAFE
    violations: List[str] = []

    if balance < hard_buffer:
        zone = FEES_UNDER_HARD_BUFFER
        violations.append(FEES_UNDER_HARD_BUFFER)
    elif balance < soft_buffer:
        zone = FEES_UNDER_BUFFER
        violations.append(FEES_UNDER_BUFFER)
    elif balance > dynamic_cap:
        zone = FEES_OVER_CAP
        violations.append(FEES_OVER_CAP)
    else:
        zone = FEES_SAFE

    # ----------------------------------------------------------------
    # Logique de surplus & sweeps (préparée pour M10+, mais safe si
    # aucun paramètre sweeps n'est fourni)
    # ----------------------------------------------------------------
    target_pct_dec = _to_decimal(target_pct, default="0")
    sweep_min_dec = _to_decimal(sweep_min_usd, default="0")
    profits_share_dec = _to_decimal(profits_share_pct, default="0")
    vault_share_dec = _to_decimal(vault_share_pct, default="0")

    # Target FEES : au minimum le soft buffer, sinon max(soft, equity * target_pct)
    if total_equity > 0 and target_pct_dec > 0:
        target_fees = max(soft_buffer, total_equity * target_pct_dec)
    else:
        # Si pas de config sweeps, on vise au moins le soft buffer
        target_fees = soft_buffer

    surplus = balance - target_fees
    if surplus < 0:
        surplus = Decimal("0")

    # would_sweep : seulement si on a un surplus significatif ET un sweep_min > 0
    would_sweep = False
    cooldown_int = 0
    if cooldown_minutes is not None:
        try:
            cooldown_int = int(cooldown_minutes)
        except Exception:
            cooldown_int = 0

    if sweep_min_dec > 0 and surplus >= sweep_min_dec:
        # Respect du cooldown si fourni
        last_dt = _parse_datetime_utc(last_sweep_at)
        if last_dt is None or cooldown_int <= 0:
            would_sweep = True
        else:
            now = _now_utc()
            elapsed_min = (now - last_dt).total_seconds() / 60.0
            if elapsed_min >= cooldown_int:
                would_sweep = True

    # On stocke last_sweep_at sous forme de str ISO UTC si possible
    last_sweep_iso: Optional[str]
    parsed_last = _parse_datetime_utc(last_sweep_at)
    if parsed_last is None:
        last_sweep_iso = None
    else:
        last_sweep_iso = parsed_last.isoformat()

    state = FeesState(
        wallet_id=wallet_id,
        balance_usd=float(balance),
        hard_buffer_usd=float(hard_buffer),
        soft_buffer_usd=float(soft_buffer),
        dynamic_cap_usd=float(dynamic_cap),
        zone=zone,
        violations=violations,
        target_pct=float(target_pct_dec),
        target_fees_usd=float(target_fees),
        surplus_usd=float(surplus),
        would_sweep=would_sweep,
        sweep_min_usd=float(sweep_min_dec),
        profits_share_pct=float(profits_share_dec),
        vault_share_pct=float(vault_share_dec),
        cooldown_minutes=cooldown_int,
        last_sweep_at=last_sweep_iso,
    )

    return state.to_dict()


# ------------------------------------------------------------
# LIVE GATE M10 (10.2.3)
# ------------------------------------------------------------

def compute_live_gate(
    *,
    safety_cfg: Dict[str, Any],
    finance_snapshot: Dict[str, Any],
    execution_runtime: Dict[str, Any],
    force_locked: bool = True,
) -> Dict[str, Any]:
    """
    Helper LIVE gate M10 (pré-LIVE).

    safety_cfg vient typiquement de config.json :

      safety_cfg = cfg["finance"]["policies"]["safety_guards"]

    avec des clés comme :
      - warning_drawdown_pct
      - critical_drawdown_pct
      - max_consecutive_losers_warning
      - max_consecutive_losers_critical
      - min_operational_capital_usd

    finance_snapshot doit contenir au minimum :
      - "equity_total_usd"
      - éventuellement "fees_state" avec "zone"
      - éventuellement "alerts" / "critical"
      - éventuellement "risk_wallets" avec "over_cap"

    execution_runtime est un snapshot du runtime d'exécution, par ex. :

      execution_runtime = {
        "daily_drawdown_pct": ...,
        "consecutive_losers": ...,
        "kill_switch": ...,
        "hard_stop_active": ...,
      }
    """

    reasons: List[str] = []
    checks: Dict[str, Any] = {}

    # --------------------------------------------------------
    # PRÉ-LIVE : verrou global
    # --------------------------------------------------------
    reasons.append("M10_NOT_VALIDATED")
    reasons.append("PAPER_ONLY_MODE")

    # --------------------------------------------------------
    # Capital minimal
    # --------------------------------------------------------
    min_capital = _to_decimal(safety_cfg.get("min_operational_capital_usd", 0))
    equity_total = _to_decimal(finance_snapshot.get("equity_total_usd", 0))

    checks["equity_total_usd"] = float(equity_total)
    checks["min_operational_capital_usd"] = float(min_capital)

    if equity_total < min_capital:
        reasons.append("CAPITAL_BELOW_MIN_OPERATIONAL")

    # --------------------------------------------------------
    # Drawdown du jour
    # --------------------------------------------------------
    critical_dd = _to_decimal(safety_cfg.get("critical_drawdown_pct", 100))
    dd_pct = _to_decimal(execution_runtime.get("daily_drawdown_pct", 0))

    checks["daily_drawdown_pct"] = float(dd_pct)
    checks["critical_drawdown_pct"] = float(critical_dd)

    if critical_dd > 0 and dd_pct >= critical_dd:
        reasons.append("DAILY_DRAWDOWN_ABOVE_CRITICAL")

    # --------------------------------------------------------
    # Streak de pertes
    # --------------------------------------------------------
    streak = execution_runtime.get("consecutive_losers")
    if streak is None:
        streak = execution_runtime.get("losing_streak", 0)

    try:
        streak = int(streak or 0)
    except Exception:
        streak = 0

    streak_crit_raw = safety_cfg.get("max_consecutive_losers_critical")
    try:
        streak_crit = int(streak_crit_raw) if streak_crit_raw is not None else None
    except Exception:
        streak_crit = None

    checks["consecutive_losers"] = streak
    checks["max_consecutive_losers_critical"] = streak_crit

    if streak_crit is not None and streak >= streak_crit:
        reasons.append("CONSECUTIVE_LOSERS_ABOVE_CRITICAL")

    # --------------------------------------------------------
    # Kill switch / hard stop
    # --------------------------------------------------------
    ks_raw = execution_runtime.get("kill_switch")

    # Deux formats possibles :
    # - bool simple
    # - dict { enabled: bool, tripped: bool, reason: str | None }
    if isinstance(ks_raw, dict):
        ks_tripped = bool(ks_raw.get("tripped"))
    else:
        ks_tripped = bool(ks_raw)

    kill_switch = bool(
        ks_tripped
        or execution_runtime.get("hard_stop_active")
        or execution_runtime.get("kill_switch_tripped")
    )
    checks["kill_switch"] = kill_switch

    if kill_switch:
        reasons.append("KILL_SWITCH_ACTIVE")

    # --------------------------------------------------------
    # Zone FEES (fees_state)
    # --------------------------------------------------------
    fees_state = finance_snapshot.get("fees_state") or {}
    fees_zone = fees_state.get("zone")

    checks["fees_zone"] = fees_zone

    if fees_zone in {
        FEES_UNDER_HARD_BUFFER,
        FEES_UNDER_BUFFER,
        FEES_OVER_CAP,
    }:
        # On remonte directement le code de zone comme raison
        reasons.append(str(fees_zone))

    # --------------------------------------------------------
    # Risk wallets (caps en % d'equity)
    # --------------------------------------------------------
    risk_wallets = finance_snapshot.get("risk_wallets") or []
    over_cap_ids: List[str] = []

    if isinstance(risk_wallets, list):
        for rw in risk_wallets:
            try:
                over = bool(rw.get("over_cap"))
            except Exception:
                over = False
            if over:
                wid = str(rw.get("wallet_id") or "?")
                over_cap_ids.append(wid)

    checks["risk_wallets_over_cap"] = over_cap_ids

    if over_cap_ids:
        reasons.append("RISK_WALLET_OVER_CAP")

    # --------------------------------------------------------
    # Alerte finance CRITICAL globale éventuelle
    # --------------------------------------------------------
    alerts = finance_snapshot.get("alerts") or {}
    critical_alerts = alerts.get("critical") or []

    critical_codes: List[str] = []
    if isinstance(critical_alerts, list):
        critical_codes = [str(x) for x in critical_alerts]
    elif isinstance(critical_alerts, dict):
        for v in critical_alerts.values():
            if isinstance(v, list):
                critical_codes.extend(str(x) for x in v)
    elif isinstance(critical_alerts, str):
        critical_codes = [critical_alerts]

    checks["finance_critical_alerts"] = critical_codes

    if critical_codes:
        reasons.append("FINANCE_ALERTS_CRITICAL")

    # --------------------------------------------------------
    # allowed / blocked
    # --------------------------------------------------------
    if force_locked:
        allowed = False
    else:
        allowed = len(reasons) == 0

    return {
        "allowed": allowed,
        "reasons": reasons,
        "checks": checks,
    }

