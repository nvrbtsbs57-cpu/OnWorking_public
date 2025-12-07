# scripts/test_memecoin_signals_sol.py

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests

# --- Project root hack (same style as other scripts) -------------------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

DEFAULT_TRADES_URL = os.getenv(
    "BOT_TRADES_URL",
    "http://127.0.0.1:8001/godmode/trades",
)


# ----------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------


def fetch_trades(
    url: str,
    limit: int = 50,
    timeout: float = 5.0,
) -> Optional[List[Dict[str, Any]]]:
    """
    Fetch trades list from /godmode/trades.
    """
    logger = logging.getLogger("test_memecoin_signals_sol")
    try:
        resp = requests.get(url, params={"limit": limit}, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("HTTP error on %s: %s", url, exc)
        return None

    data = resp.json()
    # FastAPI godmode endpoint usually returns {"items": [...]}
    items = data.get("items", data)
    if not isinstance(items, list):
        logger.warning("Unexpected format for /godmode/trades: %r", data)
        return None
    return items


def filter_sol_trades(
    trades: List[Dict[str, Any]],
    symbol: str = "SOL/USDC",
    chain: str = "solana",
) -> List[Dict[str, Any]]:
    """
    Filter only SOL/USDC trades on Solana chain.
    """
    res: List[Dict[str, Any]] = []
    for t in trades:
        t_symbol = t.get("symbol")
        t_chain = t.get("chain")
        if t_symbol == symbol and t_chain == chain:
            res.append(t)
    return res


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Watch paper trades SOL/USDC (chain=solana) "
            "via FastAPI GODMODE (/godmode/trades)."
        )
    )

    parser.add_argument(
        "--trades-url",
        default=DEFAULT_TRADES_URL,
        help="URL of /godmode/trades (default: %(default)s or env BOT_TRADES_URL).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max trades per poll (default: 100).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Watch duration in seconds (default: 60).",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Interval between polls in seconds (default: 5.0).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )

    return parser.parse_args()


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("test_memecoin_signals_sol")

    logger.info("=== TEST_MEMECOIN_SIGNALS_SOL (watcher) ===")
    logger.info("Trades endpoint: %s", args.trades_url)
    logger.info(
        "Watching for %ds (poll every %.1fs, limit=%d)",
        args.duration,
        args.interval,
        args.limit,
    )

    seen_ids = set()
    start = time.time()

    while True:
        now = time.time()
        if now - start > args.duration:
            break

        trades = fetch_trades(args.trades_url, limit=args.limit)
        if trades is None:
            logger.warning("Could not fetch trades, retrying...")
        else:
            sol_trades = filter_sol_trades(trades)

            for t in sol_trades:
                ts = (
                    t.get("ts")
                    or t.get("timestamp")
                    or t.get("time")
                    or t.get("created_at")
                )
                symbol = t.get("symbol")
                chain = t.get("chain")
                wallet = t.get("wallet_id") or t.get("wallet")
                side = t.get("side")

                notional = (
                    t.get("notional_usd")
                    or t.get("size_usd")
                    or t.get("notional")
                    or t.get("size")
                    or t.get("qty")
                )
                pnl = (
                    t.get("realized_pnl_usd")
                    or t.get("pnl_usd")
                    or t.get("pnl")
                )

                trade_id = (
                    t.get("id")
                    or t.get("trade_id")
                    or f"{ts}-{symbol}-{side}"
                )
                if trade_id in seen_ids:
                    continue
                seen_ids.add(trade_id)

                logger.info(
                    "NEW SOL TRADE: ts=%s chain=%s symbol=%s wallet=%s "
                    "side=%s notional_usd=%s pnl=%s raw=%s",
                    ts,
                    chain,
                    symbol,
                    wallet,
                    side,
                    notional,
                    pnl,
                    t,
                )

        time.sleep(args.interval)

    logger.info(
        "Done. Unique SOL/USDC trades seen: %d",
        len(seen_ids),
    )
    logger.info("=== END TEST_MEMECOIN_SIGNALS_SOL ===")


if __name__ == "__main__":
    main()
