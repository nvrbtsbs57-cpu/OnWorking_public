#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from typing import Any, Dict

try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover
    print(
        "[monitor_m10_finance] Le module 'requests' est requis. "
        "Installe-le avec: pip install requests",
        file=sys.stderr,
    )
    sys.exit(1)

API_BASE = "http://127.0.0.1:8001"


def _fetch_json(path: str) -> Dict[str, Any]:
    url = f"{API_BASE}{path}"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Réponse non-JSON depuis {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Réponse inattendue depuis {url}: type={type(data)}")
    return data


def fetch_status() -> Dict[str, Any]:
    return _fetch_json("/godmode/status")


def fetch_trades_runtime() -> Dict[str, Any]:
    return _fetch_json("/godmode/trades/runtime")


def _fmt_bool(x: Any) -> str:
    return "✅" if bool(x) else "❌"


def _fmt_money(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return str(x)
    return f"{v:,.2f} $".replace(",", " ")


def _fmt_pct(x: Any) -> str:
    try:
        v = float(x)
    except Exception:
        return str(x)
    return f"{v * 100:.2f} %"


def print_status_once() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 80)
    print(f"[M10 MONITOR] Snapshot GODMODE — {now}")
    print("=" * 80)

    # ------------------------------------------------------------------
    # 1) /godmode/status
    # ------------------------------------------------------------------
    try:
        status = fetch_status()
    except Exception as exc:
        print(f"[ERREUR] Impossible d'appeler /godmode/status: {exc}", file=sys.stderr)
        return

    mode = status.get("mode", "UNKNOWN")
    bot_mode = status.get("bot_mode", mode)
    run_mode = status.get("run_mode", "unknown")
    exec_mode = status.get("execution_mode", "unknown")
    safety_mode = status.get("safety_mode", "unknown")
    bot_name = status.get("bot_name", "BOT_GODMODE")
    profile = status.get("profile", "UNKNOWN")
    finance_profile = status.get("finance_profile", profile)

    equity_total = status.get("equity_total_usd", 0.0)
    wallets_count = status.get("wallets_count", 0)

    print(f"Bot        : {bot_name}")
    print(f"Profile    : {profile} (finance_profile={finance_profile})")
    print(f"Mode       : mode={mode}, bot_mode={bot_mode}")
    print(f"Run/Exec   : run_mode={run_mode}, execution_mode={exec_mode}")
    print(f"Safety     : safety_mode={safety_mode}")
    print(f"Wallets    : {wallets_count} wallets, equity_total={_fmt_money(equity_total)}")
    print("-" * 80)

    # ------------------------------------------------------------------
    # 2) Finance
    # ------------------------------------------------------------------
    finance = status.get("finance") or {}
    if not isinstance(finance, dict):
        finance = {}

    fin_equity = finance.get("equity_total_usd", equity_total)
    fees_state = finance.get("fees_state") or {}
    fees_zone = fees_state.get("zone", "UNKNOWN")
    fees_balance = fees_state.get("balance_usd", 0.0)
    fees_surplus = fees_state.get("surplus_usd", 0.0)
    fees_hard = fees_state.get("hard_buffer_usd", 0.0)
    fees_soft = fees_state.get("soft_buffer_usd", 0.0)
    fees_cap = fees_state.get("dynamic_cap_usd", 0.0)

    print("[FINANCE]")
    print(f"  Equity totale         : {_fmt_money(fin_equity)}")

    print(
        f"  FEES wallet ({fees_state.get('wallet_id','fees')}): "
        f"balance={_fmt_money(fees_balance)}, "
        f"zone={fees_zone}, "
        f"buffers=[hard={_fmt_money(fees_hard)}, soft={_fmt_money(fees_soft)}], "
        f"cap_dynamique={_fmt_money(fees_cap)}"
    )

    print(
        f"    surplus_usd={_fmt_money(fees_surplus)}, "
        f"violations={fees_state.get('violations', [])}"
    )

    # fees_sweep_preview
    sweep_prev = finance.get("fees_sweep_preview") or {}
    if sweep_prev:
        would_sweep = sweep_prev.get("would_sweep", False)
        print(
            f"  Sweep preview         : would_sweep={_fmt_bool(would_sweep)}, "
            f"surplus={_fmt_money(sweep_prev.get('surplus_usd', 0.0))}, "
            f"sweep_amount={_fmt_money(sweep_prev.get('sweep_amount_usd', 0.0))}"
        )
        if would_sweep:
            print(
                f"    -> to_profits={_fmt_money(sweep_prev.get('to_profits_usd', 0.0))}, "
                f"to_vault={_fmt_money(sweep_prev.get('to_vault_usd', 0.0))}"
            )

    # risk_wallets
    risk_wallets = finance.get("risk_wallets") or []
    if isinstance(risk_wallets, list) and risk_wallets:
        print("  Risk wallets :")
        for rw in risk_wallets:
            wid = rw.get("wallet_id") or rw.get("id") or "?"
            bal = rw.get("balance_usd", 0.0)
            pct = rw.get("equity_pct", 0.0)
            max_pct = rw.get("max_equity_pct", 0.0)
            over_cap = bool(rw.get("over_cap", False))
            tag = "OVER_CAP" if over_cap else "OK"
            print(
                f"    - {wid:<10} balance={_fmt_money(bal)} "
                f"equity_pct={pct:.2f} %, max={max_pct:.2f} % [{tag}]"
            )

    # capital_guard
    cap_guard = finance.get("capital_guard") or {}
    if cap_guard:
        cap_min = cap_guard.get("min_operational_capital_usd")
        cap_eq = cap_guard.get("equity_total_usd", fin_equity)
        below_min = cap_guard.get("below_min", False)
        print(
            "  Capital guard         : "
            f"equity={_fmt_money(cap_eq)}, "
            f"min_operational={_fmt_money(cap_min)}, "
            f"below_min={_fmt_bool(below_min)}"
        )

    # alerts
    alerts = finance.get("alerts") or {}
    critical = alerts.get("critical") or []
    warning = alerts.get("warning") or []
    nb_crit = len(critical) if isinstance(critical, list) else 0
    nb_warn = len(warning) if isinstance(warning, list) else 0
    print(f"  Alerts finance        : critical={nb_crit}, warning={nb_warn}")
    if nb_crit:
        print("    CRITICAL codes :", critical)
    if nb_warn:
        print("    WARNING codes  :", warning)

    print("-" * 80)

    # ------------------------------------------------------------------
    # 3) LIVE gate M10
    # ------------------------------------------------------------------
    gate = status.get("live_gate_m10") or {}
    allowed = gate.get("allowed", False)
    reasons = gate.get("reasons") or []
    checks = gate.get("checks") or {}

    print("[LIVE GATE M10]")
    print(f"  allowed               : {_fmt_bool(allowed)}")
    print(f"  reasons               : {reasons}")

    if isinstance(checks, dict):
        eq = checks.get("equity_total_usd")
        min_cap = checks.get("min_operational_capital_usd")
        dd = checks.get("daily_drawdown_pct")
        crit_dd = checks.get("critical_drawdown_pct")
        streak = checks.get("consecutive_losers")
        streak_crit = checks.get("max_consecutive_losers_critical")
        fees_zone_chk = checks.get("fees_zone")
        over_cap_ids = checks.get("risk_wallets_over_cap")
        fin_crit_alerts = checks.get("finance_critical_alerts")

        print(
            "  checks.equity         : "
            f"equity={_fmt_money(eq)}, min_operational={_fmt_money(min_cap)}"
        )
        print(
            "  checks.drawdown       : "
            f"dd={_fmt_pct(dd)}, critical={_fmt_pct(crit_dd)}"
        )
        print(
            "  checks.streak         : "
            f"consecutive_losers={streak}, "
            f"max_crit={streak_crit}"
        )
        print(f"  checks.fees_zone      : {fees_zone_chk}")
        print(f"  checks.risk_over_cap  : {over_cap_ids}")
        print(f"  checks.finance_alerts : {fin_crit_alerts}")

    # ------------------------------------------------------------------
    # 4) PnL simulé
    # ------------------------------------------------------------------
    pnl_sim = status.get("pnl_simulated") or {}
    day_real = pnl_sim.get("day_realized_usd", 0.0)
    day_fees = pnl_sim.get("day_fees_usd", 0.0)

    print("-" * 80)
    print("[PNL SIMULÉ]")
    print(
        f"  day_realized_usd      : {_fmt_money(day_real)} "
        f"(en M10 on accepte ~0 tant que pas branché)"
    )
    print(f"  day_fees_usd          : {_fmt_money(day_fees)}")

    # ------------------------------------------------------------------
    # 5) /godmode/trades/runtime
    # ------------------------------------------------------------------
    try:
        tr = fetch_trades_runtime()
    except Exception as exc:
        print(f"[ERREUR] Impossible d'appeler /godmode/trades/runtime: {exc}")
        return

    trade_count = tr.get("trade_count", 0)
    volume_usd = tr.get("volume_usd", 0.0)
    fees_total_usd = tr.get("fees_total_usd", 0.0)
    pnl_realized = tr.get("pnl_realized_usd", 0.0)
    pnl_unrealized = tr.get("pnl_unrealized_usd", 0.0)

    print("-" * 80)
    print("[TRADES RUNTIME]")
    print(f"  trade_count           : {trade_count}")
    print(f"  volume_usd            : {_fmt_money(volume_usd)}")
    print(f"  fees_total_usd        : {_fmt_money(fees_total_usd)}")
    print(f"  pnl_realized_usd      : {_fmt_money(pnl_realized)}")
    print(f"  pnl_unrealized_usd    : {_fmt_money(pnl_unrealized)}")
    print("=" * 80)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitoring M10 GODMODE (finance + LIVE gate + trades)."
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help=(
            "Si >0, rafraîchit le snapshot toutes les N secondes. "
            "Si 0 (défaut), affiche un seul snapshot et quitte."
        ),
    )
    args = parser.parse_args()

    if args.interval <= 0:
        print_status_once()
        return

    try:
        while True:
            print_status_once()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[monitor_m10_finance] Arrêt demandé par l'utilisateur.")


if __name__ == "__main__":
    main()

