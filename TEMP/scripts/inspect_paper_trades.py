#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/inspect_paper_trades.py

Lis le fichier de trades papier (data/godmode/trades.jsonl par défaut)
et affiche un petit récap :
  - nombre de trades
  - volume par marché (chain:symbol)
  - derniers trades

Usage :
  python scripts/inspect_paper_trades.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

import sys

BASE_DIR = Path(__file__).resolve().parent.parent
TRADES_PATH = BASE_DIR / "data" / "godmode" / "trades.jsonl"


def load_trades(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        print(f"Aucun fichier de trades trouvé à {path}")
        return []

    trades: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except Exception:
                continue
    return trades


def main() -> None:
    print(f"Lecture des trades papier depuis : {TRADES_PATH}")
    trades = load_trades(TRADES_PATH)

    if not trades:
        print("Aucun trade trouvé.")
        return

    nb = len(trades)
    print(f"\nNombre total de trades : {nb}")

    volume_per_market: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    side_counts: Dict[str, int] = defaultdict(int)

    for t in trades:
        chain = str(t.get("chain") or "unknown")
        symbol = str(t.get("symbol") or t.get("market") or "UNKNOWN")
        market = f"{chain}:{symbol}"

        notional_raw = t.get("notional") or t.get("notional_usd") or 0
        try:
            notional = Decimal(str(notional_raw))
        except Exception:
            notional = Decimal("0")

        volume_per_market[market] += notional

        side = str(t.get("side") or "").lower()
        side_counts[side] += 1

    print("\nVolume total par marché (notional en USD approximatif) :")
    for market, vol in sorted(volume_per_market.items(), key=lambda x: str(x[0])):
        print(f"  - {market:<20} : {vol}")

    print("\nRépartition des sides :")
    for side, cnt in side_counts.items():
        print(f"  - {side or 'unknown'} : {cnt}")

    print("\nDerniers trades :")
    for t in trades[-10:]:
        print(
            f"- {t.get('created_at')} | "
            f"{t.get('chain')} {t.get('symbol')} | "
            f"{t.get('side')} | "
            f"notional={t.get('notional')} | price={t.get('price')}"
        )


if __name__ == "__main__":
    main()
