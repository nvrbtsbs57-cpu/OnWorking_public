from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        print(f"[ERROR] config.json introuvable : {path}")
        sys.exit(1)

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON invalide dans {path}: {e}")
        sys.exit(1)


def get_wallet_names(cfg: Dict[str, Any]) -> Set[str]:
    wallets = cfg.get("wallets", [])
    names = {w.get("name") for w in wallets if isinstance(w, dict) and "name" in w}
    return {n for n in names if n is not None}


def check_required_sections(cfg: Dict[str, Any], errors: List[str]) -> None:
    required = ["wallets", "wallet_roles", "finance", "wallet_flows", "risk", "strategies"]
    for key in required:
        if key not in cfg:
            errors.append(f"Section manquante dans config.json : '{key}'")


def check_wallet_roles(cfg: Dict[str, Any], wallet_names: Set[str], errors: List[str]) -> None:
    roles = cfg.get("wallet_roles", {})
    if not isinstance(roles, dict):
        errors.append("wallet_roles doit être un objet JSON")
        return

    def check_role_map(role_name: str, role_map: Any) -> None:
        if not isinstance(role_map, dict):
            errors.append(f"wallet_roles.{role_name} doit être un objet")
            return
        for chain, wallet_id in role_map.items():
            if wallet_id not in wallet_names:
                errors.append(
                    f"wallet_roles.{role_name}['{chain}'] référence un wallet inconnu: '{wallet_id}'"
                )

    for role_name, role_map in roles.items():
        check_role_map(role_name, role_map)


def check_finance_profile(cfg: Dict[str, Any], errors: List[str], warnings: List[str]) -> None:
    finance = cfg.get("finance", {})
    profile = finance.get("profile")
    if profile != "LIVE_150":
        warnings.append(f"finance.profile = '{profile}', attendu 'LIVE_150' pour ce setup.")


def check_initial_balances(cfg: Dict[str, Any], wallet_names: Set[str], errors: List[str]) -> None:
    finance = cfg.get("finance", {})
    wallets_cfg = finance.get("wallets", {})
    init_balances = wallets_cfg.get("initial_balances_usd", {})

    if not isinstance(init_balances, dict):
        errors.append("finance.wallets.initial_balances_usd doit être un objet JSON")
        return

    for wallet_id in init_balances.keys():
        if wallet_id not in wallet_names:
            errors.append(
                f"finance.wallets.initial_balances_usd référence un wallet inconnu: '{wallet_id}'"
            )


def check_wallet_flows(cfg: Dict[str, Any], wallet_names: Set[str], errors: List[str]) -> None:
    flows = cfg.get("wallet_flows", {})
    if not flows:
        return

    auto_fees_wallet = flows.get("auto_fees_wallet_id")
    if auto_fees_wallet and auto_fees_wallet not in wallet_names:
        errors.append(
            f"wallet_flows.auto_fees_wallet_id référence un wallet inconnu: '{auto_fees_wallet}'"
        )

    rules = flows.get("profit_split_rules", [])
    if not isinstance(rules, list):
        errors.append("wallet_flows.profit_split_rules doit être une liste")
        return

    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            errors.append(f"wallet_flows.profit_split_rules[{i}] doit être un objet")
            continue

        src = rule.get("source_wallet_id")
        tgt = rule.get("target_wallet_id")
        if src not in wallet_names:
            errors.append(
                f"wallet_flows.profit_split_rules[{i}].source_wallet_id inconnu: '{src}'"
            )
        if tgt not in wallet_names:
            errors.append(
                f"wallet_flows.profit_split_rules[{i}].target_wallet_id inconnu: '{tgt}'"
            )


def main() -> None:
    # On suppose que le script est lancé depuis la racine du projet:
    # python scripts/check_config.py
    repo_root = Path(__file__).resolve().parent.parent
    config_path = repo_root / "config.json"

    print(f"[INFO] Chargement de {config_path} ...")
    cfg = load_config(config_path)

    errors: List[str] = []
    warnings: List[str] = []

    wallet_names = get_wallet_names(cfg)
    if not wallet_names:
        errors.append("Aucun wallet défini dans config['wallets'].")

    check_required_sections(cfg, errors)
    check_wallet_roles(cfg, wallet_names, errors)
    check_finance_profile(cfg, errors, warnings)
    check_initial_balances(cfg, wallet_names, errors)
    check_wallet_flows(cfg, wallet_names, errors)

    print()
    if errors:
        print("=== CONFIG CHECK: ERREURS ===")
        for e in errors:
            print(f" - {e}")
        print(f"\n[RESULT] ❌ Config invalide ({len(errors)} erreur(s)).")
        sys.exit(1)

    if warnings:
        print("=== CONFIG CHECK: WARNINGS ===")
        for w in warnings:
            print(f" - {w}")
        print()

    print("[RESULT] ✅ config.json valide et cohérent avec les wallets / flows définis.")
    if warnings:
        print(f"[RESULT] (avec {len(warnings)} avertissement(s)).")


if __name__ == "__main__":
    main()
