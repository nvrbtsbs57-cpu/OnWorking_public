# scripts/test_memecoin_strategy_stub.py

from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal

# --- Hack chemin projet (comme les autres scripts) --------------------
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.strategies.memecoin_farming.agent import (  # type: ignore
    MemecoinStrategyEngine,
    MemecoinCandidate,
    make_default_pair_configs,
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)5s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("test_memecoin_strategy_stub")

    pair_cfgs = make_default_pair_configs()
    engine = MemecoinStrategyEngine(pair_configs=pair_cfgs, logger_=logger)

    # On simule quelques "candidats" memecoins
    candidates = [
        MemecoinCandidate(
            symbol="SOL/USDC",
            chain="SOL",
            score=0.9,
            notional_usd=Decimal("200"),
            wallet_id="sniper_sol",
            meta={"debug": "high_score"},
        ),
        MemecoinCandidate(
            symbol="SOL/USDC",
            chain="SOL",
            score=0.4,
            notional_usd=Decimal("20"),  # sous min_notional, devrait être filtré
            wallet_id="sniper_sol",
            meta={"debug": "too_small"},
        ),
    ]

    logger.info("Feed de %d candidats dans le moteur memecoin.", len(candidates))
    engine.feed_candidates(candidates)

    signals = engine.generate_signals()
    logger.info("Signals générés (%d) :", len(signals))
    for s in signals:
        logger.info(
            "- id=%s wallet=%s symbol=%s kind=%s side=%s notional=%.2f meta=%s",
            s.id,
            s.wallet_id,
            s.symbol,
            s.kind.value,
            s.side.value,
            s.notional_usd,
            s.meta,
        )


if __name__ == "__main__":
    main()
