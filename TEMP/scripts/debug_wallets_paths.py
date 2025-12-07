#!/usr/bin/env python
# scripts/debug_wallets_paths.py
#
# Petit script pour vérifier si RuntimeWalletManager et le dashboard
# lisent/écrivent le *même* fichier wallets_runtime.json.

from __future__ import annotations

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap PYTHONPATH (même logique que test_live150_finance.py)
# ---------------------------------------------------------------------------

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[1]  # ../ (racine du repo BOT_GODMODE)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bot.wallets.runtime_manager import RUNTIME_WALLETS_PATH  # type: ignore
import bot.api.godmode_dashboard as gd  # type: ignore


def inspect(path: Path, label: str) -> None:
    path = path.resolve()
    print(f"\n[{label}] path={path}  exists={path.exists()}")
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    print("  updated_at:", data.get("updated_at"))
    print("  equity_total_usd:", data.get("equity_total_usd"))

    wallets = data.get("wallets") or {}
    sniper = None
    copy_ = None

    if isinstance(wallets, dict):
        sniper = wallets.get("sniper_sol")
        copy_ = wallets.get("copy_sol")
    else:
        for w in wallets:
            if w.get("wallet_id") == "sniper_sol":
                sniper = w
            if w.get("wallet_id") == "copy_sol":
                copy_ = w

    for name, w in (("sniper_sol", sniper), ("copy_sol", copy_)):
        if not w:
            continue
        bal = w.get("balance_usd")
        pnl = (
            w.get("realized_pnl_today_usd")
            or w.get("pnl_today_usd")
            or 0.0
        )
        print(f"  {name}: balance={bal} pnl_today={pnl}")


def main() -> None:
    print("RuntimeWalletManager → RUNTIME_WALLETS_PATH :")
    inspect(RUNTIME_WALLETS_PATH, "RuntimeWalletManager")

    print("\nDashboard → _RUNTIME_PATH :")
    inspect(gd._RUNTIME_PATH, "godmode_dashboard")


if __name__ == "__main__":
    main()

