#!/usr/bin/env python
import time
from datetime import datetime
from typing import Any, Dict

import requests

BASE_URL = "http://127.0.0.1:8001"


def fetch(path: str) -> Dict[str, Any]:
    url = f"{BASE_URL}{path}"
    r = requests.get(url, timeout=10)  # timeout un peu plus large
    r.raise_for_status()
    return r.json()


def extract_wallet_balances(wallets_json: Dict[str, Any]) -> Dict[str, float]:
    """Construit un dict {wallet_id: balance_usd} en gérant int/float/str."""
    balances: Dict[str, float] = {}
    for w in wallets_json.get("wallets", []):
        wallet_id = w.get("wallet_id") or w.get("id") or w.get("name")
        if not wallet_id:
            continue
        bal = w.get("balance_usd")
        if isinstance(bal, (int, float)):
            balances[wallet_id] = float(bal)
        elif isinstance(bal, str):
            try:
                balances[wallet_id] = float(bal)
            except ValueError:
                # On ignore si ce n'est pas convertible
                continue
    return balances


def print_header() -> None:
    print("=" * 80)
    print("[M10] TEST WALLET_FLOWS LIVE_150")
    print("=" * 80)


def print_snapshot(
    baseline_wallets: Dict[str, Any],
    baseline_balances: Dict[str, float],
) -> None:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    wallets = fetch("/godmode/wallets/runtime")
    status = fetch("/godmode/status")

    # /godmode/trades/runtime peut être lourd → on protège
    try:
        trades = fetch("/godmode/trades/runtime")
    except requests.exceptions.RequestException as e:
        print("\n[WARN] Impossible de récupérer /godmode/trades/runtime :")
        print(f"  {e}")
        trades = {}

    finance = wallets.get("finance", {}) or {}
    summary = wallets.get("summary", {}) or {}
    alerts_root = wallets.get("alerts", {}) or {}

    fees_wallet = finance.get("fees_wallet", {}) or {}
    risk_wallets = finance.get("risk_wallets", []) or []
    planned_transfers = finance.get("planned_transfers", []) or []
    live_gate_finance = finance.get("live_gate", {}) or {}
    alerts_finance = alerts_root.get("finance", []) or []

    balances = extract_wallet_balances(wallets)

    print("\n" + "-" * 80)
    print(f"[{now}] SNAPSHOT RUNTIME / WALLET_FLOWS")
    print("-" * 80)

    # 1) Résumé global
    total_capital = summary.get("total_capital_usd")
    equity_total = finance.get("equity_total_usd")
    live_allowed_global = status.get("live_allowed")
    live_blocked_reasons = status.get("live_blocked_reasons", []) or []

    pnl_today_status = status.get("pnl_today_total_usd")
    pnl_today_wallets = finance.get("pnl_today_total_usd")

    print(f"total_capital_usd = {total_capital}")
    print(f"equity_total_usd = {equity_total}")
    print(f"pnl_today_total_usd (status)  = {pnl_today_status}")
    print(f"pnl_today_total_usd (wallets) = {pnl_today_wallets}")
    print(f"live_allowed_global = {live_allowed_global}")
    if live_blocked_reasons:
        print(f"live_blocked_reasons = {live_blocked_reasons}")

    # 1b) Gate finance (live_gate pur runtime wallets)
    lag = live_gate_finance.get("live_allowed")
    lbr = live_gate_finance.get("blocked_reasons", []) or []
    print(f"live_allowed_finance = {lag}")
    if lbr:
        print(f"live_gate_finance.blocked_reasons = {lbr}")

    # 2) Trades runtime (compteur + aggregated fields)
    print("\n[TRADES RUNTIME]")
    if trades:
        trade_count = trades.get("trade_count")
        if trade_count is None:
            trade_count = trades.get("count")

        volume_usd = trades.get("volume_usd")
        pnl_realized = trades.get("pnl_realized_usd")
        pnl_unrealized = trades.get("pnl_unrealized_usd")

        pnl_today_from_wallets = trades.get("pnl_today_total_usd")
        fees_total = trades.get("fees_total_usd")
        fees_today = trades.get("fees_paid_today_total_usd")

        print(f"trade_count              = {trade_count}")
        print(f"volume_usd               = {volume_usd}")
        print(f"pnl_realized_usd         = {pnl_realized}")
        print(f"pnl_unrealized_usd       = {pnl_unrealized}")
        print(f"pnl_today_total_usd      = {pnl_today_from_wallets}")
        print(f"fees_total_usd           = {fees_total}")
        print(f"fees_paid_today_total_usd= {fees_today}")
    else:
        print("Impossible de lire /godmode/trades/runtime (voir WARN ci-dessus).")

    # 3) Wallets importants : delta vs baseline
    interesting = [
        "sniper_sol",
        "copy_sol",
        "base_main",
        "bsc_main",
        "fees",
        "vault",
        "emergency",
        "profits_sol",
        "profits_base",
        "profits_bsc",
    ]

    print("\n[WALLETS CLÉS – Δ depuis baseline]")
    for wid in interesting:
        b0 = baseline_balances.get(wid)
        b1 = balances.get(wid)
        if b0 is None and b1 is None:
            continue
        if b0 is not None and b1 is not None:
            delta = b1 - b0
        else:
            delta = "NA"
        print(
            f"- {wid:12s}: "
            f"start={b0 if b0 is not None else 'NA':>8} "
            f"now={b1 if b1 is not None else 'NA':>8} "
            f"Δ={delta:>8}"
        )

    # 4) Fees wallet
    print("\n[FEES WALLET]")
    if fees_wallet:
        fw_id = fees_wallet.get("id") or fees_wallet.get("wallet_id") or "fees"
        fw_bal = fees_wallet.get("balance_usd")
        fw_pct = fees_wallet.get("equity_pct")
        fw_min = fees_wallet.get("min_buffer_usd")
        fw_max_pct = fees_wallet.get("max_equity_pct")
        fw_viol = fees_wallet.get("violations", [])

        print(f"id              = {fw_id}")
        print(f"balance_usd     = {fw_bal}")
        print(f"equity_pct      = {fw_pct}")
        print(f"min_buffer_usd  = {fw_min}")
        print(f"max_equity_pct  = {fw_max_pct}")
        print(f"violations      = {fw_viol}")
    else:
        print("fees_wallet: (aucune info dans finance.fees_wallet)")

    # 5) Risk wallets
    print("\n[RISK WALLETS]")
    if not risk_wallets:
        print("Aucun risk_wallet configuré dans finance.risk_wallets.")
    else:
        for rw in risk_wallets:
            wid = rw.get("wallet_id") or rw.get("id")
            eq_pct = rw.get("equity_pct")
            max_eq = rw.get("max_equity_pct")
            viol = rw.get("violations", [])
            print(
                f"- {wid}: equity_pct={eq_pct}, "
                f"max_equity_pct={max_eq}, violations={viol}"
            )

    # 6) Planned transfers (flows concrets)
    print("\n[PLANNED TRANSFERS]")
    if not planned_transfers:
        print("Aucun planned_transfer.")
    else:
        for t in planned_transfers:
            reason = t.get("reason", "?")
            from_id = (
                t.get("from_wallet_id")
                or t.get("from")
                or t.get("source_wallet_id")
            )
            to_id = (
                t.get("to_wallet_id")
                or t.get("to")
                or t.get("target_wallet_id")
            )
            amount = t.get("amount_usd")
            print(f"- {reason}: {from_id} -> {to_id} {amount} USD")

    # 7) Alerts finance
    print("\n[FINANCE ALERTS]")
    if not alerts_finance:
        print("Aucune alert finance.")
    else:
        for a in alerts_finance:
            level = a.get("level")
            code = a.get("code")
            wallet_id = a.get("wallet_id")
            msg = a.get("msg") or a.get("message") or a.get("detail")
            if wallet_id:
                print(
                    f"- level={level} code={code} wallet={wallet_id} "
                    f"msg={msg}"
                )
            else:
                print(f"- level={level} code={code} msg={msg}")


def main() -> None:
    print_header()
    print("Récupération baseline /godmode/wallets/runtime ...")
    baseline_wallets = fetch("/godmode/wallets/runtime")
    baseline_balances = extract_wallet_balances(baseline_wallets)
    print("Baseline capturée.")
    print("Laisse tourner le runtime M10 (memecoin + copy).")
    print("Ce script va afficher un snapshot toutes les 30s. Ctrl+C pour arrêter.\n")

    try:
        while True:
            print_snapshot(baseline_wallets, baseline_balances)
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nArrêt demandé par l'utilisateur.")


if __name__ == "__main__":
    main()

