from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

logger = logging.getLogger("godmode_dashboard")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

router = APIRouter(prefix="/godmode", tags=["godmode"])

# ----------------------------------------------------------------------
# Paths de base
# ----------------------------------------------------------------------
CONFIG_PATH = Path("config.json")

# Fichiers runtime produits par le bot
_RUNTIME_PATH = Path("data/godmode/wallets_runtime.json")
_EXECUTION_RUNTIME_PATH = Path("data/godmode/execution_runtime.json")
_TRADES_RUNTIME_PATH = Path("data/godmode/trades_runtime.json")  # optionnel pour fallback
_PROFILE_STATE_PATH = Path("data/godmode/session_profile.json")

# ⚙️ Configuration finance minimale par défaut (fallback)
_FINANCE_CFG: Dict[str, Any] = {
    "profile_id": "LIVE_150",
    # wallet dédié aux fees opérationnelles / gas
    "fees_wallet_id": "fees",
    "fees_min_buffer_usd": 200.0,  # ex: 200$ minimum pour garantir gas & ops
    "fees_max_equity_pct": 0.10,  # max 10% de l'equity totale
    # wallets considérés "risk-on" avec un cap en % d'equity
    "risk_wallets": [
        {"wallet_id": "sniper_sol", "max_equity_pct": 0.03},  # 3% max
    ],
    # cibles des transferts logiques (sweeps)
    "sweep_targets": {
        "fees_over_cap": "vault",  # surplus de fees vers vault
    },
    # capital de référence par défaut (fallback)
    "capital_usd": 150.0,
    # safety guards fallback vide (sera enrichi via config.finance.policies.safety_guards)
    "safety_guards": {},
}

_FINANCE_CFG_LOADED_FROM_FILE = False


# ======================================================================
# Helpers génériques
# ======================================================================


def _to_float(x: Any) -> float:
    """Conversion robuste vers float (gère strings, None, etc.)."""
    try:
        return float(x)
    except Exception:
        return 0.0


def _load_config() -> Dict[str, Any]:
    """Lecture brute de config.json (shape libre, on est défensif)."""
    if not CONFIG_PATH.exists():
        logger.warning("config.json introuvable (%s)", CONFIG_PATH)
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.exception("Impossible de lire config.json: %s", exc)
        return {}


def _load_profile_state() -> Dict[str, Any]:
    """Lecture de data/godmode/session_profile.json (peut être vide)."""
    if not _PROFILE_STATE_PATH.exists():
        return {}
    try:
        return json.loads(_PROFILE_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.exception("Impossible de lire %s: %s", _PROFILE_STATE_PATH, exc)
        return {}


def _get_finance_cfg() -> Dict[str, Any]:
    """
    Charge la configuration finance à partir de config.json (section "finance"),
    en surchargeant les valeurs par défaut de _FINANCE_CFG.

    Supporte plusieurs structures possibles :
    - config["finance"]
    - config["mode_profiles"][profile_id]["finance"]
    - config["profiles"][profile_id]["finance"]
    """
    global _FINANCE_CFG, _FINANCE_CFG_LOADED_FROM_FILE

    if _FINANCE_CFG_LOADED_FROM_FILE:
        return _FINANCE_CFG

    cfg = dict(_FINANCE_CFG)  # copie des defaults

    try:
        raw_cfg = _load_config()
        if not raw_cfg:
            logger.warning(
                "config.json introuvable ou vide, utilisation des valeurs finance par défaut."
            )
        else:
            # 1) déterminer le profil courant
            profile_id_root = raw_cfg.get("profile_id") or cfg.get("profile_id") or "GODMODE"

            # 2) essayer de trouver le bloc "finance" dans différentes structures
            finance: Dict[str, Any] = raw_cfg.get("finance") or {}

            if not finance:
                # mode_profiles / profiles
                mode_profiles = raw_cfg.get("mode_profiles") or raw_cfg.get("profiles") or {}
                profile_cfg = (
                    mode_profiles.get(profile_id_root)
                    or mode_profiles.get("GODMODE")
                    or {}
                )
                finance = profile_cfg.get("finance") or {}

            if not isinstance(finance, dict):
                finance = {}

            # profile_id : finance.profile_id > root.profile_id > default
            profile_id = (
                finance.get("profile_id")
                or finance.get("profile")
                or profile_id_root
                or cfg.get("profile_id")
            )
            if profile_id:
                cfg["profile_id"] = profile_id

            # fees_policy peut être dans finance["fees_policy"], avec fallback sur finance direct
            fees_policy = finance.get("fees_policy") or {}

            fees_wallet_id = (
                fees_policy.get("fees_wallet_id")
                or finance.get("fees_wallet_id")
                or cfg.get("fees_wallet_id")
            )
            if fees_wallet_id:
                cfg["fees_wallet_id"] = fees_wallet_id

            def _num(source: Dict[str, Any], key: str, default: float) -> float:
                try:
                    return float(source.get(key, default))
                except Exception:
                    return default

            cfg["fees_min_buffer_usd"] = _num(
                fees_policy,
                "min_buffer_usd",
                cfg.get("fees_min_buffer_usd", 0.0),
            )
            cfg["fees_max_equity_pct"] = _num(
                fees_policy,
                "max_equity_pct",
                cfg.get("fees_max_equity_pct", 1.0),
            )

            # risk_wallets : finance["risk_wallets"] si présent
            risk_wallets = finance.get("risk_wallets")
            if isinstance(risk_wallets, list):
                norm: List[Dict[str, Any]] = []
                for rw in risk_wallets:
                    if not isinstance(rw, dict):
                        continue
                    wid = rw.get("wallet_id")
                    if not wid:
                        continue
                    try:
                        max_pct = float(rw.get("max_equity_pct", 1.0))
                    except Exception:
                        max_pct = 1.0
                    norm.append(
                        {
                            "wallet_id": wid,
                            "max_equity_pct": max_pct,
                        }
                    )
                if norm:
                    cfg["risk_wallets"] = norm

            # sweep_targets : finance.fees_policy.sweep_targets > finance.sweep_targets > default
            sweep_targets = (
                fees_policy.get("sweep_targets")
                or finance.get("sweep_targets")
                or cfg.get("sweep_targets", {})
            )
            cfg["sweep_targets"] = sweep_targets

            # capital_usd : capital notionnel de référence du profil
            capital = finance.get("capital_usd")
            if capital is not None:
                try:
                    cfg["capital_usd"] = float(capital)
                except Exception:
                    pass

            # safety_guards : finance.policies.safety_guards
            policies = finance.get("policies") or {}
            safety = policies.get("safety_guards") or {}
            if isinstance(safety, dict):
                sg: Dict[str, float] = {}
                for key in [
                    "warning_drawdown_pct",
                    "critical_drawdown_pct",
                    "max_consecutive_losers_warning",
                    "max_consecutive_losers_critical",
                    "min_operational_capital_usd",
                ]:
                    if key in safety:
                        try:
                            sg[key] = float(safety[key])
                        except Exception:
                            continue
                if sg:
                    cfg["safety_guards"] = sg

    except Exception as e:
        logger.exception("Erreur lors du chargement de config.json pour finance: %s", e)

    _FINANCE_CFG = cfg
    _FINANCE_CFG_LOADED_FROM_FILE = True
    return _FINANCE_CFG


def _load_runtime_raw() -> Dict[str, Any]:
    """
    Lecture brute de wallets_runtime.json.

    On normalise pour obtenir au minimum :
    {
        "updated_at": "...",
        "wallets": { "wallet_id": { ... }, ... }
    }

    Supporte deux formats possibles :
    - ancien format : {"wallets": { "sniper_sol": {...}, ... }}
    - nouveau format : {"wallets": [ {"wallet_id": "...", ...}, ... ]}
    """
    if not _RUNTIME_PATH.exists():
        logger.warning("wallets_runtime.json absent (%s)", _RUNTIME_PATH)
        raise HTTPException(
            status_code=503,
            detail="wallets_runtime.json not found; bot runtime not started or not writing snapshots",
        )

    try:
        text = _RUNTIME_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception as e:  # JSON illisible / IO error
        logger.exception("Erreur lecture wallets_runtime.json: %s", e)
        raise HTTPException(status_code=500, detail="invalid wallets_runtime.json")

    raw_wallets = data.get("wallets")

    # Format dict direct
    if isinstance(raw_wallets, dict):
        wallets_map: Dict[str, Dict[str, Any]] = raw_wallets
    # Format liste de wallets (chacun avec wallet_id ou id)
    elif isinstance(raw_wallets, list):
        wallets_map = {}
        for item in raw_wallets:
            if not isinstance(item, dict):
                continue
            wid = item.get("wallet_id") or item.get("id")
            if not wid:
                continue
            wallets_map[str(wid)] = item
    else:
        logger.error("wallets_runtime.json ne contient pas de clé 'wallets' valide")
        raise HTTPException(
            status_code=500,
            detail="wallets_runtime.json has no valid 'wallets' key",
        )

    return {
        "updated_at": data.get("updated_at"),
        "wallets": wallets_map,
    }


def _load_execution_runtime() -> Optional[Dict[str, Any]]:
    """
    Lit execution_runtime.json si présent.
    Retourne None s'il manque ou est invalide.
    """
    if not _EXECUTION_RUNTIME_PATH.exists():
        return None

    try:
        text = _EXECUTION_RUNTIME_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        logger.exception("Erreur lecture execution_runtime.json: %s", e)
        return None


# ======================================================================
# Finance runtime → snapshot enrichi
# ======================================================================


def _compute_finance_from_runtime(runtime: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforme le JSON runtime simple en snapshot finance enrichi :
    - equity_total_usd
    - état du wallet fees (buffer + cap)
    - caps sur quelques wallets risqués
    - garde-fous globaux (drawdown, losing streak, capital mini)
    - planned_transfers (sweeps logiques, pas d'exécution)
    - live_gate (OK / bloqué + raisons)
    - alerts (liste de dicts)
    """
    finance_cfg = _get_finance_cfg()

    wallets_raw = runtime.get("wallets", {}) or {}
    updated_at = runtime.get("updated_at")

    # 1) Equity totale + normalisation des wallets
    equity_total = 0.0
    wallet_states: Dict[str, Dict[str, Any]] = {}

    for wid, w in wallets_raw.items():
        # balance : balance_usd ou equity_usd
        bal_raw = w.get("balance_usd")
        if bal_raw is None:
            bal_raw = w.get("equity_usd")
        bal = _to_float(bal_raw)
        equity_total += bal

        wallet_states[wid] = {
            "id": wid,
            "balance_usd": bal,
            "equity_usd": bal,
            "realized_pnl_today_usd": _to_float(
                w.get("realized_pnl_today_usd", w.get("pnl_today_usd", 0.0))
            ),
            "gross_pnl_today_usd": _to_float(w.get("gross_pnl_today_usd", 0.0)),
            "fees_paid_today_usd": _to_float(w.get("fees_paid_today_usd", 0.0)),
            "consecutive_losing_trades": int(_to_float(w.get("consecutive_losing_trades", 0))),
            "last_reset_date": w.get("last_reset_date"),
            # On propage aussi quelques champs bruts si présents (chain, role, flags, etc.)
            "chain": w.get("chain") or w.get("network"),
            "role": w.get("role"),
            "flags": w.get("flags") or {},
        }

    if equity_total < 0:
        equity_total = 0.0

    # Pourcentages d'equity par wallet
    for wid, st in wallet_states.items():
        bal = st["balance_usd"]
        st["equity_pct"] = (bal / equity_total) if equity_total > 0 else 0.0

    alerts: List[Dict[str, Any]] = []
    planned_transfers: List[Dict[str, Any]] = []

    # 2) Politique du wallet de fees (buffer & cap)
    fees_wallet_id = finance_cfg.get("fees_wallet_id", "fees")
    fees_min_buffer_usd = float(finance_cfg.get("fees_min_buffer_usd", 0.0))
    fees_max_equity_pct = float(finance_cfg.get("fees_max_equity_pct", 1.0))

    fees_state: Optional[Dict[str, Any]] = None

    if fees_wallet_id in wallet_states:
        fs = wallet_states[fees_wallet_id]
        violations: List[str] = []
        bal = fs["balance_usd"]
        pct = fs["equity_pct"]

        # Buffer minimal en nominal
        if bal < fees_min_buffer_usd:
            violations.append("UNDER_BUFFER")
            alerts.append(
                {
                    "level": "WARNING",
                    "code": "FEES_UNDER_BUFFER",
                    "wallet_id": fees_wallet_id,
                    "msg": f"{fees_wallet_id} {bal:.2f} < min buffer {fees_min_buffer_usd:.2f} USD",
                }
            )

        # Cap en % d'equity
        if equity_total > 0 and pct > fees_max_equity_pct:
            violations.append("OVER_CAP")
            alerts.append(
                {
                    "level": "CRITICAL",
                    "code": "FEES_OVER_CAP",
                    "wallet_id": fees_wallet_id,
                    "msg": f"{fees_wallet_id} {pct:.4%} > cap {fees_max_equity_pct:.4%}",
                }
            )

            # Plan de sweep logique du surplus vers un autre wallet (ex: vault)
            target = finance_cfg.get("sweep_targets", {}).get("fees_over_cap", "vault")
            cap_amount = equity_total * fees_max_equity_pct
            excess = max(0.0, bal - cap_amount)
            if excess > 0:
                planned_transfers.append(
                    {
                        "from": fees_wallet_id,
                        "to": target,
                        "amount_usd": excess,
                        "reason": "fees_cap_exceeded",
                    }
                )

        fees_state = {
            "id": fees_wallet_id,
            "balance_usd": bal,
            "equity_pct": pct,
            "min_buffer_usd": fees_min_buffer_usd,
            "max_equity_pct": fees_max_equity_pct,
            "violations": violations,
        }

    # 3) Caps sur wallets risqués (ex: sniper_sol max 3% d'equity)
    risk_wallets_cfg = finance_cfg.get("risk_wallets", []) or []
    risk_wallets_state: List[Dict[str, Any]] = []

    for rw_cfg in risk_wallets_cfg:
        wid = rw_cfg.get("wallet_id")
        max_pct = float(rw_cfg.get("max_equity_pct", 1.0))
        st = wallet_states.get(wid)
        if not st:
            continue

        violations: List[str] = []
        pct = st["equity_pct"]

        if pct > max_pct and equity_total > 0:
            violations.append("OVER_CAP")
            alerts.append(
                {
                    "level": "CRITICAL",
                    "code": "RISK_WALLET_OVER_CAP",
                    "wallet_id": wid,
                    "msg": f"{wid} {pct:.4%} > cap {max_pct:.4%}",
                }
            )

        risk_wallets_state.append(
            {
                "id": wid,
                "balance_usd": st["balance_usd"],
                "equity_pct": pct,
                "max_equity_pct": max_pct,
                "violations": violations,
            }
        )

    # 4) Garde-fous globaux (drawdown, losing streak, capital mini)
    safety_cfg = finance_cfg.get("safety_guards", {}) or {}
    capital_usd = float(finance_cfg.get("capital_usd", 0.0) or 0.0)

    # Drawdown global vs capital_usd (0–1)
    if capital_usd > 0:
        drawdown_ratio = max(0.0, (capital_usd - equity_total) / capital_usd)
        if drawdown_ratio > 1.0:
            drawdown_ratio = 1.0
    else:
        drawdown_ratio = 0.0

    warning_dd = float(safety_cfg.get("warning_drawdown_pct", 0.0) or 0.0)
    critical_dd = float(safety_cfg.get("critical_drawdown_pct", 0.0) or 0.0)

    if critical_dd > 0 and drawdown_ratio >= critical_dd:
        alerts.append(
            {
                "level": "CRITICAL",
                "code": "GLOBAL_DRAWDOWN_CRITICAL",
                "msg": f"Drawdown global {drawdown_ratio:.2%} >= seuil critique {critical_dd:.2%}",
            }
        )
    elif warning_dd > 0 and drawdown_ratio >= warning_dd:
        alerts.append(
            {
                "level": "WARNING",
                "code": "GLOBAL_DRAWDOWN_WARNING",
                "msg": f"Drawdown global {drawdown_ratio:.2%} >= seuil warning {warning_dd:.2%}",
            }
        )

    # Losing streak global (max sur tous les wallets)
    max_consec = 0
    for st in wallet_states.values():
        try:
            n = int(st.get("consecutive_losing_trades", 0))
        except Exception:
            n = 0
        if n > max_consec:
            max_consec = n

    warn_los = safety_cfg.get("max_consecutive_losers_warning")
    crit_los = safety_cfg.get("max_consecutive_losers_critical")

    if isinstance(crit_los, (int, float)) and max_consec >= crit_los:
        alerts.append(
            {
                "level": "CRITICAL",
                "code": "GLOBAL_CONSECUTIVE_LOSERS_CRITICAL",
                "msg": f"Série de pertes consécutives globale {max_consec} >= seuil critique {crit_los}",
            }
        )
    elif isinstance(warn_los, (int, float)) and max_consec >= warn_los:
        alerts.append(
            {
                "level": "WARNING",
                "code": "GLOBAL_CONSECUTIVE_LOSERS_WARNING",
                "msg": f"Série de pertes consécutives globale {max_consec} >= seuil warning {warn_los}",
            }
        )

    # Capital opérationnel minimum
    min_oper_cap = safety_cfg.get("min_operational_capital_usd")
    if isinstance(min_oper_cap, (int, float)) and equity_total < float(min_oper_cap):
        alerts.append(
            {
                "level": "CRITICAL",
                "code": "OPERATIONAL_CAPITAL_TOO_LOW",
                "msg": f"Equity totale {equity_total:.2f} < capital opérationnel minimum {min_oper_cap:.2f}",
            }
        )

    # 5) Gate LIVE (runtime pur, sans la policy M10)
    blocked_reasons: List[str] = []

    if equity_total <= 0:
        blocked_reasons.append("equity_total_zero")

    # Toute alerte CRITICAL bloque le LIVE (runtime)
    for a in alerts:
        if a.get("level") == "CRITICAL":
            code = a.get("code", "UNKNOWN")
            blocked_reasons.append(f"critical_{code}")

    live_gate = {
        "live_allowed": len(blocked_reasons) == 0,
        "blocked_reasons": blocked_reasons,
    }

    return {
        "equity_total_usd": equity_total,
        "wallets_state": wallet_states,
        "fees_wallet": fees_state,
        "risk_wallets": risk_wallets_state,
        "planned_transfers": planned_transfers,
        "live_gate": live_gate,
        "alerts": alerts,
        "updated_at": updated_at,
    }


# ======================================================================
# Helpers trades runtime
# ======================================================================


def _trade_to_runtime_dict(trade: Any) -> Dict[str, Any]:
    """
    Convertit un objet Trade (dataclass / modèle) en dict sérialisable.
    On essaie aussi de produire un 'reason' humainement lisible
    à partir de trade.reason + trade.meta (stratégie, type de signal, etc.).
    """

    def _get(name: str, default: Any = None) -> Any:
        return getattr(trade, name, default)

    def _f(x: Any) -> Optional[float]:
        if x is None:
            return None
        try:
            return float(x)
        except Exception:
            return None

    ts = _get("ts")
    if ts is not None:
        try:
            ts_str = ts.isoformat()
        except Exception:
            ts_str = str(ts)
    else:
        ts_str = None

    side = _get("side")
    # side peut être un Enum (side.value) ou une simple str
    if hasattr(side, "value"):
        side_str = str(side.value)
    elif side is None:
        side_str = None
    else:
        side_str = str(side)

    meta = _get("meta", None)
    raw_reason = _get("reason")

    # Construire une raison lisible à partir de meta si raw_reason est vide
    reason = raw_reason
    if not reason and isinstance(meta, dict):
        tags: List[str] = []

        # on essaie plusieurs clés courantes pour décrire "ce que le bot farm"
        for key in (
            "strategy",
            "strategy_name",
            "signal_type",
            "source",
            "pool_type",
            "pool_name",
            "token_symbol",
        ):
            val = meta.get(key)
            if val:
                tags.append(str(val))

        if tags:
            reason = " / ".join(tags)

    if not reason:
        reason = "-"

    return {
        "id": _get("id"),
        "ts": ts_str,
        "chain": _get("chain"),
        "symbol": _get("symbol"),
        "side": side_str,
        "qty": _f(_get("qty")),
        "price": _f(_get("price")),
        "notional_usd": _f(
            getattr(trade, "notional_usd", None)
            or (_get("qty") or 0) * (_get("price") or 0)
        ),
        # ici tu verras apparaître la stratégie / type de farm dans le dashboard
        "reason": reason,
        "meta": meta,
    }


def _load_recent_trades_from_store(limit: int = 50) -> List[Any]:
    """
    Essaie de charger les derniers trades via TradeStore / TradeStoreConfig.

    On importe ici pour éviter de casser le router si le module trading n'est
    pas dispo dans un contexte particulier (tests unitaires isolés, etc.).
    """
    try:
        from bot.trading.store import TradeStore, TradeStoreConfig  # type: ignore
    except Exception as e:
        logger.warning("TradeStore non disponible (%s), fallback fichier JSON si présent.", e)
        return []

    cfg = TradeStoreConfig()  # PaperTradingEngine utilise déjà ce défaut
    store = TradeStore(cfg)

    trades: List[Any]

    if hasattr(store, "get_recent_trades"):
        trades = list(store.get_recent_trades(limit=limit))
    elif hasattr(store, "get_recent"):
        trades = list(store.get_recent(limit=limit))
    elif hasattr(store, "load_all"):
        all_trades = list(store.load_all())  # type: ignore[arg-type]
        trades = all_trades[-limit:]
    elif hasattr(store, "list_trades"):
        all_trades = list(store.list_trades())  # type: ignore[arg-type]
        trades = all_trades[-limit:]
    else:
        logger.warning("TradeStore ne fournit aucune méthode connue pour lire les trades.")
        trades = []

    # On met les plus récents en premier
    trades = list(reversed(trades))
    return trades


def _load_recent_trades(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Charge les derniers trades runtime et les convertit en dicts pour le dashboard.

    Stratégie :
      1) essayer de passer par TradeStore (source de vérité)
      2) sinon, fallback sur un éventuel fichier JSON (trades_runtime.json)
    """
    trades_obj = _load_recent_trades_from_store(limit=limit)

    # Fallback fichier JSON si le store renvoie [] et qu'un snapshot existe.
    if not trades_obj and _TRADES_RUNTIME_PATH.exists():
        try:
            raw = json.loads(_TRADES_RUNTIME_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("trades"), list):
                trades_obj = raw["trades"]
        except Exception as e:
            logger.exception("Erreur en lisant %s: %s", _TRADES_RUNTIME_PATH, e)

    # Normalisation → dict dashboard
    trades_dicts: List[Dict[str, Any]] = []

    for t in trades_obj:
        # Si c'est déjà un dict (cas fallback JSON), on suppose au bon format.
        if isinstance(t, dict):
            trades_dicts.append(t)
        else:
            trades_dicts.append(_trade_to_runtime_dict(t))

    return trades_dicts


# ======================================================================
# Endpoints API
# ======================================================================


@router.get("/wallets/runtime")
def get_wallets_runtime() -> Dict[str, Any]:
    """
    Snapshot runtime pour le bloc WALLETS du dashboard.

    Shape simple et compatible avec ce que le front attend déjà :

      {
        "wallets_source": "runtime",
        "profile_id": "LIVE_150",
        "profile": "LIVE_150",
        "equity_total_usd": 150.0,
        "wallets_count": 10,
        "wallets": [ ... ],
        "updated_at": "..."
      }
    """
    finance_cfg = _get_finance_cfg()
    runtime = _load_runtime_raw()  # {"updated_at": "...", "wallets": {id -> state}}
    wallets_raw = runtime.get("wallets", {}) or {}
    updated_at = runtime.get("updated_at")

    wallets_list: List[Dict[str, Any]] = []
    total_equity = 0.0

    for wid, st in wallets_raw.items():
        # balance = equity_usd ou balance_usd
        bal = _to_float(st.get("equity_usd", st.get("balance_usd", 0.0)))
        pnl = _to_float(
            st.get("realized_pnl_today_usd")
            or st.get("pnl_today_usd")
            or st.get("gross_pnl_today_usd")
        )

        # nombre de positions ouvertes si dispo
        open_pos_raw = (
            st.get("open_positions")
            or st.get("open_positions_count")
            or 0
        )
        try:
            open_positions = int(open_pos_raw)
        except Exception:
            open_positions = 0

        total_equity += bal

        wallets_list.append(
            {
                "wallet_id": wid,
                "role": st.get("role") or "generic",
                "chain": st.get("chain") or st.get("network") or "UNKNOWN",
                "address": st.get("address"),
                "tags": st.get("tags", []),
                "balance_usd": bal,
                "equity_usd": bal,
                "pnl_today_usd": pnl,
                "open_positions": open_positions,
            }
        )

    profile_id = finance_cfg.get("profile_id", "UNKNOWN")

    return {
        "wallets_source": "runtime",
        "profile_id": profile_id,
        "profile": profile_id,  # pour les anciens bouts de front qui lisent "profile"
        "equity_total_usd": total_equity,
        "wallets_count": len(wallets_list),
        "wallets": wallets_list,
        "updated_at": updated_at,
    }


@router.get("/alerts/finance")
def get_finance_alerts() -> Dict[str, Any]:
    """
    Endpoint dédié aux alertes finance (pour un panneau dédié côté UI).
    """
    finance_cfg = _get_finance_cfg()
    runtime = _load_runtime_raw()
    finance = _compute_finance_from_runtime(runtime)

    return {
        "source": "runtime",
        "profile_id": finance_cfg.get("profile_id", "UNKNOWN"),
        "updated_at": finance.get("updated_at"),
        "alerts": finance["alerts"],
    }


@router.get("/trades/runtime")
def get_trades_runtime(limit: int = 50) -> Dict[str, Any]:
    """
    Retourne les derniers trades exécutés (runtime / PAPER) pour affichage dashboard.

    - limit: nombre max de trades (les plus récents d'abord)
    """
    finance_cfg = _get_finance_cfg()

    try:
        trades = _load_recent_trades(limit=limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erreur lors du chargement des trades runtime: %s", e)
        raise HTTPException(status_code=500, detail="unable_to_load_trades_runtime")

    return {
        "source": "runtime",
        "profile_id": finance_cfg.get("profile_id", "UNKNOWN"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(trades),
        "trades": trades,
    }


@router.get("/status")
def get_status() -> Dict[str, Any]:
    """
    Statut global du GODMODE du point de vue finance / wallets + exécution.
    Utilisé pour un bandeau en haut du dashboard.
    """
    finance_cfg = _get_finance_cfg()
    profile_state = _load_profile_state()

    profile_id = finance_cfg.get("profile_id", "UNKNOWN")

    try:
        runtime = _load_runtime_raw()
        finance = _compute_finance_from_runtime(runtime)
        live_gate = finance.get("live_gate", {})

        # Lecture de l'état d'exécution (RiskEngine / KillSwitchState) depuis execution_runtime.json
        exec_runtime = _load_execution_runtime()

        if exec_runtime is None:
            execution_block: Dict[str, Any] = {
                "risk_enabled": False,
                "kill_switch": {
                    "enabled": False,
                    "tripped": True,
                    "reason": "execution_runtime_unavailable",
                },
                "daily_drawdown_pct": None,
                "soft_stop_active": True,
                "hard_stop_active": True,
            }
        else:
            # On renvoie tel quel tout ce que le runtime écrit (kill switch, day_state, etc.)
            execution_block = exec_runtime

        status: Dict[str, Any] = {
            "wallets_source": "runtime",
            "profile_id": profile_id,
            "profile": profile_id,
            "updated_at": finance.get("updated_at"),
            "equity_total_usd": finance.get("equity_total_usd"),
            "live_allowed": live_gate.get("live_allowed"),
            "live_blocked_reasons": live_gate.get("blocked_reasons", []),
            "execution": execution_block,
            "profile_state": profile_state,
        }

    except HTTPException as exc:
        status = {
            "wallets_source": "unavailable",
            "profile_id": profile_id,
            "profile": profile_id,
            "updated_at": None,
            "equity_total_usd": 0.0,
            "live_allowed": False,
            "live_blocked_reasons": [f"runtime_unavailable:{exc.status_code}"],
            "execution": {
                "risk_enabled": False,
                "kill_switch": {
                    "enabled": False,
                    "tripped": True,
                    "reason": "runtime_unavailable",
                },
                "daily_drawdown_pct": None,
                "soft_stop_active": True,
                "hard_stop_active": True,
            },
            "profile_state": profile_state,
        }

    return status


@router.get("/ping")
def ping() -> Dict[str, str]:
    """Petit endpoint de santé pour vérifier que le router godmode est monté."""
    return {"status": "ok", "component": "godmode_dashboard"}


@router.get("/ui", include_in_schema=False)
def godmode_ui_redirect():
    """
    Redirige /godmode/ui vers le front statique (index.html).
    """
    return RedirectResponse(url="/static/index.html")
