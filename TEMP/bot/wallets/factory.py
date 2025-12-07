# bot/wallets/factory.py
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .engine import WalletFlowsEngine
from .models import ProfitSplitRule, WalletConfig, WalletFlowsConfig, WalletRole


def _extract_wallets_conf(conf: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Normalise la section config["wallets"].

    On accepte deux formes :

    1) Forme avancée (dict) :

        "wallets": {
          "enabled": true,
          "definitions": [
            {...},
            {...}
          ]
        }

    2) Forme simple (liste) :

        "wallets": [
          { "name": "sniper_sol", "role": "SCALPING", "chain": "solana", ... },
          { "name": "copy_trader", "role": "COPY_TRADING", "chain": "base", ... }
        ]

    Dans ce cas :
      - on considère que c'est "enabled" par défaut,
      - aucun auto_fees / profit_split n'est configuré (valeurs par défaut).
    """
    wallets_conf = conf.get("wallets")
    if wallets_conf is None:
        raise ValueError("Section 'wallets' absente de config.json")

    # Cas 1 : liste simple
    if isinstance(wallets_conf, list):
        defs = wallets_conf
        meta: Dict[str, Any] = {}
        return defs, meta

    # Cas 2 : dict avancé
    if isinstance(wallets_conf, dict):
        enabled = bool(wallets_conf.get("enabled", True))
        if not enabled:
            raise ValueError("Wallets désactivés dans config.json (wallets.enabled = false)")

        defs = wallets_conf.get("definitions") or []
        if not isinstance(defs, list):
            raise ValueError("wallets.definitions doit être une liste de définitions de wallets")

        meta = wallets_conf
        return defs, meta

    raise TypeError("Section 'wallets' doit être un dict ou une liste dans config.json")


def _build_wallet_configs(
    defs: Iterable[Dict[str, Any]],
    initial_balances_usd: Optional[Dict[str, Any]] = None,
    per_wallet_risk: Optional[Dict[str, Any]] = None,
) -> List[WalletConfig]:
    """
    Construit la liste des WalletConfig à partir des définitions.

    On essaie d'être tolérant :
      - id : "id" ou "wallet_id" ou "name"
      - base_ccy : "base_ccy" ou "base_currency"
      - role : string mappée vers WalletRole (fallback OTHER)
      - initial_balance_usd :
            1) si finance.wallets[...] fournit un montant pour ce wallet_id, on le prend,
            2) sinon la clé "initial_balance_usd" dans la définition du wallet,
            3) sinon fallback 1000 (paper / debug).
      - max_risk_pct_per_trade :
            1) wallet_def.max_risk_pct_per_trade si présent,
            2) sinon risk.wallets[wallet_id].max_pct_balance_per_trade,
            3) sinon fallback 1 %.
      - max_daily_loss_pct :
            1) wallet_def.max_daily_loss_pct si présent,
            2) sinon risk.wallets[wallet_id].max_daily_loss_pct,
            3) sinon None.
    """
    wallet_cfgs: List[WalletConfig] = []
    initial_balances_usd = initial_balances_usd or {}
    per_wallet_risk = per_wallet_risk or {}

    for raw in defs:
        if not isinstance(raw, dict):
            continue

        # id logique du wallet (dans ta config: "name": "sniper_sol", etc.)
        wid = str(
            raw.get("id") or raw.get("wallet_id") or raw.get("name") or ""
        ).strip()
        if not wid:
            # Ignorer les entrées sans id
            continue

        role_raw = str(raw.get("role", "other")).lower()
        # on accepte soit les values, soit les names, sinon OTHER
        try:
            # essai sur value
            role = WalletRole(role_raw)  # type: ignore[arg-type]
        except ValueError:
            try:
                role = WalletRole[role_raw.upper()]
            except Exception:
                role = WalletRole.OTHER

        chain = str(raw.get("chain", raw.get("network", ""))).strip()
        base_ccy = str(
            raw.get("base_ccy", raw.get("base_currency", "USD"))
        ).upper()

        # Config de risque par wallet (section config["risk"]["wallets"])
        risk_cfg: Dict[str, Any] = {}
        if wid in per_wallet_risk:
            rc = per_wallet_risk[wid]
            if isinstance(rc, dict):
                risk_cfg = rc

        # Balance initiale :
        # 1) si finance.wallets[...] fournit un montant pour ce wallet_id, on le prend,
        # 2) sinon on regarde "initial_balance_usd" dans la définition du wallet,
        # 3) sinon fallback 1000 USD (paper / debug).
        if wid in initial_balances_usd:
            initial_balance_usd = Decimal(str(initial_balances_usd[wid]))
        else:
            initial_balance_usd_raw = raw.get("initial_balance_usd")
            if initial_balance_usd_raw is None:
                initial_balance_usd = Decimal("1000")
            else:
                initial_balance_usd = Decimal(str(initial_balance_usd_raw))

        min_balance_usd = Decimal(str(raw.get("min_balance_usd", "0")))

        # max_risk_pct_per_trade : déf. locale > risk.wallets[...] > 1 %
        max_risk_pct_raw = (
            raw.get("max_risk_pct_per_trade")
            or risk_cfg.get("max_pct_balance_per_trade")
            or "1"
        )
        max_risk_pct_per_trade = Decimal(str(max_risk_pct_raw))

        # max_daily_loss_pct : déf. locale > risk.wallets[...] > None
        max_daily_loss_raw = raw.get("max_daily_loss_pct")
        if max_daily_loss_raw is None:
            max_daily_loss_raw = risk_cfg.get("max_daily_loss_pct")
        if max_daily_loss_raw is None:
            max_daily_loss_pct = None
        else:
            max_daily_loss_pct = Decimal(str(max_daily_loss_raw))

        allow_outflows = bool(raw.get("allow_outflows", True))
        is_auto_fees_target = bool(raw.get("is_auto_fees_target", False))

        wallet_cfgs.append(
            WalletConfig(
                id=wid,
                role=role,
                chain=chain,
                base_ccy=base_ccy,
                initial_balance_usd=initial_balance_usd,
                min_balance_usd=min_balance_usd,
                max_risk_pct_per_trade=max_risk_pct_per_trade,
                max_daily_loss_pct=max_daily_loss_pct,
                allow_outflows=allow_outflows,
                is_auto_fees_target=is_auto_fees_target,
            )
        )

    if not wallet_cfgs:
        raise ValueError("Aucun wallet valide n'a été construit depuis config['wallets'].")

    return wallet_cfgs


def _build_profit_split_rules(meta: Dict[str, Any]) -> List[ProfitSplitRule]:
    """
    Construit les ProfitSplitRule à partir de meta["profit_split_rules"].

    meta peut être issu de config["wallet_flows"] (nouveau format)
    ou de config["wallets"] (ancien format).
    """
    rules_conf = meta.get("profit_split_rules", []) if meta else []
    rules: List[ProfitSplitRule] = []

    if not isinstance(rules_conf, list):
        return rules

    for r in rules_conf:
        if not isinstance(r, dict):
            continue

        src = str(r.get("source_wallet_id") or "").strip()
        dst = str(r.get("target_wallet_id") or "").strip()
        if not src or not dst:
            continue

        trigger_pct = Decimal(str(r.get("trigger_pct", "0")))
        percent_of_profit = Decimal(str(r.get("percent_of_profit", "100")))

        rules.append(
            ProfitSplitRule(
                source_wallet_id=src,
                target_wallet_id=dst,
                trigger_pct=trigger_pct,
                percent_of_profit=percent_of_profit,
            )
        )

    return rules


def build_wallet_engine_from_config(conf: Dict[str, Any], logger=None) -> WalletFlowsEngine:
    """
    Point d'entrée principal pour construire WalletFlowsEngine depuis config.json.

    - Gère les deux formes de "wallets" (dict avancé ou liste simple).
    - Construit les WalletConfig + WalletFlowsConfig.
    - Donne priorité à config["wallet_flows"] pour la config des flux.
    - Mappe config["risk"]["wallets"][wallet_id] vers les limites du WalletConfig.
    """
    # 1) Definitions de wallets (toujours via config["wallets"])
    defs, wallets_meta = _extract_wallets_conf(conf)

    # 1-bis) Soldes init depuis finance.wallets (supporte dict ou liste)
    finance_conf = conf.get("finance") or {}
    finance_wallets_raw = finance_conf.get("wallets")
    finance_initial_balances: Dict[str, Any] = {}

    if isinstance(finance_wallets_raw, dict):
        # Ancien format éventuel :
        #   "finance": {
        #     "wallets": {
        #       "initial_balances_usd": { "sniper_sol": 30, ... },
        #       "wallets": [ ... ]
        #     }
        #   }
        maybe_init = finance_wallets_raw.get("initial_balances_usd")
        if isinstance(maybe_init, dict):
            finance_initial_balances.update(maybe_init)

        nested_wallets = finance_wallets_raw.get("wallets")
        if isinstance(nested_wallets, list):
            for w in nested_wallets:
                if not isinstance(w, dict):
                    continue
                wid = str(
                    w.get("wallet_id") or w.get("id") or w.get("name") or ""
                ).strip()
                if not wid:
                    continue
                if "initial_balance_usd" in w:
                    finance_initial_balances[wid] = w["initial_balance_usd"]

    elif isinstance(finance_wallets_raw, list):
        # Format LIVE_150 actuel :
        #   "finance": {
        #     "wallets": [
        #       { "wallet_id": "sniper_sol", "initial_balance_usd": 30.0, ... },
        #       ...
        #     ]
        #   }
        for w in finance_wallets_raw:
            if not isinstance(w, dict):
                continue
            wid = str(
                w.get("wallet_id") or w.get("id") or w.get("name") or ""
            ).strip()
            if not wid:
                continue
            if "initial_balance_usd" in w:
                finance_initial_balances[wid] = w["initial_balance_usd"]

    elif finance_wallets_raw in (None, {}):
        # Pas de config finance.wallets explicite → on laissera le fallback 1000 USD
        finance_initial_balances = {}
    else:
        # Format inattendu, on log et on ignore.
        if logger is not None:
            logger.warning(
                "finance.wallets doit être un dict ou une liste, mais %r a été trouvé. "
                "Initial balances ignorés.",
                type(finance_wallets_raw),
            )
        finance_initial_balances = {}

    # 1-ter) Config de risque par wallet (config["risk"]["wallets"])
    risk_conf = conf.get("risk") or {}
    risk_wallets_conf = risk_conf.get("wallets") or {}

    wallet_cfgs = _build_wallet_configs(
        defs,
        initial_balances_usd=finance_initial_balances,
        per_wallet_risk=risk_wallets_conf,
    )

    # 2) Meta des flux :
    # - priorité à config["wallet_flows"] si présent
    # - sinon fallback sur meta issu de config["wallets"] (ancien format)
    flows_meta = conf.get("wallet_flows") or {}
    meta_for_flows = flows_meta or wallets_meta

    rules = _build_profit_split_rules(meta_for_flows)

    if meta_for_flows:
        auto_fees_wallet_id = meta_for_flows.get("auto_fees_wallet_id")
        min_auto_fees_pct = Decimal(str(meta_for_flows.get("min_auto_fees_pct", "2")))
        max_auto_fees_pct = Decimal(str(meta_for_flows.get("max_auto_fees_pct", "8")))
        compounding_enabled = bool(meta_for_flows.get("compounding_enabled", True))
        compounding_interval_days = int(
            meta_for_flows.get("compounding_interval_days", 3)
        )
    else:
        auto_fees_wallet_id = None
        min_auto_fees_pct = Decimal("2")
        max_auto_fees_pct = Decimal("8")
        compounding_enabled = True
        compounding_interval_days = 3

    # 2-bis) Policy fees depuis config["finance"]["fees_policy"] (optionnel)
    fees_policy = finance_conf.get("fees_policy") or {}

    # Buffer minimal conseillé
    try:
        fees_min_buffer_usd = Decimal(str(fees_policy.get("min_buffer_usd", "0")))
    except Exception:
        fees_min_buffer_usd = Decimal("0")

    # Cap en % d'equity (0.10 = 10 %) – None si absent / invalide
    fees_max_equity_pct_raw = fees_policy.get("max_equity_pct")
    if fees_max_equity_pct_raw is None:
        fees_max_equity_pct = None
    else:
        try:
            fees_max_equity_pct = Decimal(str(fees_max_equity_pct_raw))
        except Exception:
            fees_max_equity_pct = None

    # Cible de sweep quand le cap est dépassé
    sweep_targets = (
        fees_policy.get("sweep_targets")
        or finance_conf.get("sweep_targets")
        or {}
    )
    fees_over_cap_target_wallet_id = sweep_targets.get("fees_over_cap")

    flows_cfg = WalletFlowsConfig(
        auto_fees_wallet_id=auto_fees_wallet_id,
        min_auto_fees_pct=min_auto_fees_pct,
        max_auto_fees_pct=max_auto_fees_pct,
        compounding_enabled=compounding_enabled,
        compounding_interval_days=compounding_interval_days,
        profit_split_rules=rules,
        fees_min_buffer_usd=fees_min_buffer_usd,
        fees_max_equity_pct=fees_max_equity_pct,
        fees_over_cap_target_wallet_id=fees_over_cap_target_wallet_id,
    )

    return WalletFlowsEngine(wallet_cfgs, flows_cfg, logger=logger)

