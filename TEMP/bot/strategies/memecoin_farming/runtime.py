from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List

# ----------------------------------------------------------------------
# Bootstrap du projet
# ----------------------------------------------------------------------

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.strategies.memecoin_farming.agent import (  # type: ignore
    MemecoinStrategyEngine,
    MemecoinCandidate,
    make_default_pair_configs,
)
from bot.trading.paper_trader import PaperTraderConfig, PaperTrader
from bot.trading.execution import (
    ExecutionEngine as PaperExecutionEngine,
    ExecutionRequest as PaperExecutionRequest,
)
from bot.trading.models import TradeSide


# ----------------------------------------------------------------------
# Config runtime
# ----------------------------------------------------------------------


@dataclass
class RuntimeConfig:
    symbol: str = "SOL/USDC"
    chain: str = "solana"
    wallet_id: str = "sniper_sol"

    # Notional vu par la STRATÉGIE (gros pour passer les filtres)
    engine_notional_usd: Decimal = Decimal("200")

    # Notionnels réellement exécutés (profil LIVE_150)
    exec_min_notional_usd: Decimal = Decimal("2")
    exec_max_notional_usd: Decimal = Decimal("6")

    sleep_seconds: float = 5.0


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test memecoin runtime LIVE_150-like: generates paper trades "
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
        help="Execution chain name for paper engine (default: solana
