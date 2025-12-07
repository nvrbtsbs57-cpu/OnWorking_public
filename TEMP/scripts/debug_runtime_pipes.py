from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import urlopen, Request

# -------------------------------------------------------------------
# Paths locaux
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]
GODMODE_DIR = BASE_DIR / "data" / "godmode"
WALLETS_RUNTIME_PATH = GODMODE_DIR / "wallets_runtime.json"
EXECUTION_RUNTIME_PATH = GODMODE_DIR / "execution_runtime.json"
TRADES_PATH = GODMODE_DIR / "trades.jsonl"

# -------------------------------------------------------------------
# Helpers locaux
# -------------------------------------------------------------------
def _print_local_snapshot(limit_trades: int) -> None:
    print("== Local files ==")

    # wallets_runtime.json
    if not WALLETS_RUNTIME_PATH.exists():
        print(
            f"wallets_runtime.json  -> Fichier introuvable: {WALLETS_RUNTIME_PATH}"
        )
    else:
        try:
            data = json.loads(WALLETS_RUNTIME_PATH.read_text(encoding="utf-8"))
            wallets = data.get("wallets") or {}
            equity = data.get("equity_total_usd")
            print(
                f"wallets_runtime.json  -> wallets={len(wallets)}, equity={equity}"
            )
        except Exception as exc:
            print(
                f"wallets_runtime.json  -> Erreur de lecture {WALLETS_RUNTIME_PATH}: {exc}"
            )

    # execution_runtime.json
    if not EXECUTION_RUNTIME_PATH.exists():
        print(
            f"execution_runtime.json -> Fichier introuvable: {EXECUTION_RUNTIME_PATH}"
        )
    else:
        try:
            data = json.loads(EXECUTION_RUNTIME_PATH.read_text(encoding="utf-8"))
            keys = sorted(list(data.keys()))
            print(f"execution_runtime.json -> keys={keys}")
        except Exception as exc:
            print(
                f"execution_runtime.json -> Erreur de lecture {EXECUTION_RUNTIME_PATH}: {exc}"
            )

    # trades.jsonl
    if not TRADES_PATH.exists():
        print(f"trades.jsonl           -> Fichier introuvable: {TRADES_PATH}")
    else:
        try:
            lines = TRADES_PATH.read_text(encoding="utf-8").strip().splitlines()
            last = lines[-limit_trades:]
            trades: List[Dict[str, Any]] = []
            for line in last:
                line = line.strip()
                if not line:
                    continue
                try:
                    trades.append(json.loads(line))
                except Exception:
                    continue

            print(
                f"trades.jsonl           -> {len(trades)} derniers trades:"
            )
            for t in trades:
                created = t.get("created_at") or t.get("time") or "-"
                symbol = t.get("symbol") or t.get("pair") or "-"
                side = t.get("side") or "-"
                notional = t.get("notional") or t.get("notional_usd") or "-"
                status = t.get("status") or t.get("exec_status") or "-"
                print(
                    f"   - {created} | {symbol:<8} | {side:<4} | {notional} | {status}"
                )
        except Exception as exc:
            print(f"trades.jsonl           -> Erreur de lecture {TRADES_PATH}: {exc}")


# -------------------------------------------------------------------
# Helpers HTTP
# -------------------------------------------------------------------
def _fetch_json(
    base_url: str,
    path: str,
) -> Tuple[str, Any]:
    """
    Retourne (status_str, payload_ou_message).
    Ne lève jamais d'exception.
    """
    url = base_url.rstrip("/") + path
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=3) as resp:
            raw = resp.read().decode("utf-8")
        try:
            data = json.loads(raw)
            return ("OK", data)
        except Exception:
            # Réponse non JSON
            return ("NON_JSON", raw)
    except HTTPError as exc:
        return ("HTTP_ERROR", f"HTTPError {exc.code}: {exc.reason}")
    except URLError as exc:
        return ("HTTP_ERROR", f"{exc}")
    except Exception as exc:
        return ("HTTP_ERROR", f"{exc}")


def _print_api_snapshot(limit_trades: int) -> None:
    base_url = os.environ.get(
        "GODMODE_DASHBOARD_URL",
        "http://127.0.0.1:8001/godmode",  # défaut = dashboard standalone
    )

    print("== API /godmode ==")

    # /status
    status_flag, status_payload = _fetch_json(base_url, "/status")
    if status_flag != "OK" or not isinstance(status_payload, dict):
        print(
            f"GET /status          -> Erreur HTTP {base_url}/status: {status_payload}"
        )
        status_payload = {}
    else:
        print(f"GET /status          -> 200 OK")
        mode = status_payload.get("mode")
        run_mode = status_payload.get("run_mode")
        execution_mode = status_payload.get("execution_mode")
        equity = status_payload.get("equity_total_usd", status_payload.get("capital_usd"))
        wallets_count = status_payload.get("wallets_count")
        print(
            f"   mode={mode} bot_mode={status_payload.get('bot_mode')} "
            f"run_mode={run_mode} exec_mode={execution_mode} "
            f"equity_total_usd={equity} wallets_count={wallets_count}"
        )

    # /wallets/runtime
    wallets_flag, wallets_payload = _fetch_json(base_url, "/wallets/runtime")
    if wallets_flag != "OK" or not isinstance(wallets_payload, dict):
        print(
            f"GET /wallets/runtime -> Erreur HTTP {base_url}/wallets/runtime: {wallets_payload}"
        )
    else:
        print(f"GET /wallets/runtime -> 200 OK")
        wallets = wallets_payload.get("wallets") or []
        source = wallets_payload.get("wallets_source")
        eq_total = wallets_payload.get("equity_total_usd")
        print(
            f"   source={source} wallets={len(wallets)} equity_total_usd={eq_total}"
        )

    # /trades/runtime
    trades_flag, trades_payload = _fetch_json(
        base_url, f"/trades/runtime?limit={limit_trades}"
    )
    if trades_flag != "OK" or not isinstance(trades_payload, dict):
        print(
            f"GET /trades/runtime  -> Erreur HTTP {base_url}/trades/runtime?limit={limit_trades}: {trades_payload}"
        )
    else:
        print(f"GET /trades/runtime  -> 200 OK")
        trade_count = trades_payload.get("trade_count")
        volume_usd = trades_payload.get("volume_usd")
        print(
            f"   trade_count={trade_count} volume_usd={volume_usd}"
        )


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def snapshot(limit_trades: int) -> None:
    print("=" * 80)
    print(
        f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] SNAPSHOT RUNTIME / DASHBOARD"
    )
    print("-" * 80)
    _print_local_snapshot(limit_trades)
    print("-" * 80)
    _print_api_snapshot(limit_trades)
    print("=" * 80)
    print()


def main() -> None:
    # arg ultra simple: --once ou --interval N
    args = sys.argv[1:]
    limit_trades = 5
    interval = None

    if "--once" in args:
        interval = None
    else:
        # --interval N (par défaut 10s)
        if "--interval" in args:
            idx = args.index("--interval")
            try:
                interval = int(args[idx + 1])
            except Exception:
                interval = 10
        else:
            interval = 10

    if "--limit" in args:
        try:
            idx = args.index("--limit")
            limit_trades = int(args[idx + 1])
        except Exception:
            pass

    if interval is None:
        snapshot(limit_trades)
    else:
        while True:
            snapshot(limit_trades)
            time.sleep(interval)


if __name__ == "__main__":
    main()
