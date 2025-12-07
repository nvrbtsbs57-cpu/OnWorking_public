from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# Racine du projet
ROOT_DIR = Path(__file__).resolve().parents[1]

CONFIG_PATH = ROOT_DIR / "config.json"
GODMODE_DIR = ROOT_DIR / "data" / "godmode"
WALLETS_RUNTIME_PATH = GODMODE_DIR / "wallets_runtime.json"


def main() -> None:
    GODMODE_DIR.mkdir(parents=True, exist_ok=True)

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    finance = cfg.get("finance", {}) or {}
    wallets_cfg = cfg.get("wallets", []) or []
    initial_balances = (
        finance.get("wallets", {}).get("initial_balances_usd", {}) or {}
    )

    wallets = {}

    for w in wallets_cfg:
        wid = w.get("name")
        if not wid:
            continue

        bal = float(initial_balances.get(wid, 0.0) or 0.0)

        wallets[wid] = {
            "balance_usd": bal,
            "pnl_today_usd": 0.0,
            "realized_pnl_today_usd": 0.0,
            "open_positions": 0,
        }

    payload = {
        "updated_at": datetime.utcnow().isoformat(),
        "wallets": wallets,
    }

    WALLETS_RUNTIME_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("wallets_runtime.json écrit ->", WALLETS_RUNTIME_PATH)
    print("wallets =", len(wallets))


if __name__ == "__main__":
    main()
