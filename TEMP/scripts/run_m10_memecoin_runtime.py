
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from decimal import Decimal

# project root
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.strategies.memecoin_farming.runtime import build_default_runtime  # type: ignore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a small memecoin runtime that generates paper trades "
            "for SOL/USDC on Solana."
        )
    )

    parser.add_argument(
        "--symbol",
        default="SOL/USDC",
        help="Trading symbol (default: SOL/USDC).",
    )
    parser.add_argument(
        "--chain",
        default="solana",
        help="Execution chain name for paper engine (default: solana).",
    )
    parser.add_argument(
        "--wallet",
        default="sniper_sol",
        help="Logical wallet id (default: sniper_sol).",
    )
    parser.add_argument(
        "--engine-notional",
        type=float,
        default=200.0,
        help=(
            "Notional in USD used inside the strategy engine "
            "(default: 200.0)."
        ),
    )
    parser.add_argument(
        "--exec-min",
        type=float,
        default=2.0,
        help="Min execution notional in USD (default: 2.0).",
    )
    parser.add_argument(
        "--exec-max",
        type=float,
        default=6.0,
        help="Max execution notional in USD (default: 6.0).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=5.0,
        help="Sleep time between ticks in seconds (default: 5.0).",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=0,
        help="Number of ticks to run before exit. 0 means run forever.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )

    logger = logging.getLogger("run_memecoin_runtime")

    logger.info("=== RUN_MEMECOIN_RUNTIME (PAPER) ===")

    # build runtime (wallets + exec + strat) à partir de config.json
    runtime = build_default_runtime(logger_=logger)

    # Override runtime config from CLI
    runtime.config.symbol = args.symbol
    runtime.config.chain = args.chain
    runtime.config.wallet_id = args.wallet
    runtime.config.engine_notional_usd = Decimal(str(args.engine_notional))
    runtime.config.exec_min_notional_usd = Decimal(str(args.exec_min))
    runtime.config.exec_max_notional_usd = Decimal(str(args.exec_max))
    runtime.config.sleep_seconds = args.sleep

    logger.info(
        "MemecoinRuntime config: symbol=%s chain=%s wallet=%s "
        "engine_notional=%s exec_range=%s-%s sleep=%.1fs",
        runtime.config.symbol,
        runtime.config.chain,
        runtime.config.wallet_id,
        runtime.config.engine_notional_usd,
        runtime.config.exec_min_notional_usd,
        runtime.config.exec_max_notional_usd,
        runtime.config.sleep_seconds,
    )

    if args.ticks <= 0:
        # Infinite loop (Ctrl+C to stop)
        runtime.run_forever()
    else:
        logger.info("MemecoinRuntime: running %d ticks then exit.", args.ticks)
        for i in range(args.ticks):
            logger.info("MemecoinRuntime: tick %d/%d", i + 1, args.ticks)
            try:
                executed = runtime.run_once()
                logger.info(
                    "MemecoinRuntime: tick %d executed %d orders.",
                    i + 1,
                    executed,
                )
            except Exception:
                logger.exception("MemecoinRuntime: error in run_once()")
            time.sleep(runtime.config.sleep_seconds)

        logger.info("MemecoinRuntime: finished %d ticks.", args.ticks)


if __name__ == "__main__":
    main()

