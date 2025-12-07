from __future__ import annotations

import argparse
import logging
import os
import sys
import random
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, List

# ----------------------------------------------------------------------
# Bootstrap du projet
# ----------------------------------------------------------------------

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Imports projet
from bot.strategies.memecoin_farming.agent import (  # type: ignore
    MemecoinStrategyEngine,
    MemecoinCandidate,
    make_default_pair_configs,
)
from bot.trading.paper_trader import PaperTraderConfig, PaperTrader  # type: ignore
from bot.trading.execution import (  # type: ignore
    ExecutionEngine as PaperExecutionEngine,
    ExecutionRequest as PaperExecutionRequest,
)
from bot.trading.models import TradeSide  # type: ignore
from bot.core.risk import RiskConfig, RiskEngine  # type: ignore
from bot.trading.execution_risk_adapter import (  # type: ignore
    ExecutionRiskAdapter,
    RuntimeWalletStats,
)
from bot.wallets.runtime_manager import RuntimeWalletManager  # type: ignore

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


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Test memecoin PAPER trades on SOL/USDC with LIVE_150-like notionals."
        )
    )

    parser.add_argument(
        "--engine-notional",
        type=float,
        default=200.0,
        help="Notional in USD used inside the strategy engine (default: 200.0).",
    )
    parser.add_argument(
        "--exec-min",
        type=float,
        default=2.0,
        help="Minimum notional in USD for actual executions (default: 2.0).",
    )
    parser.add_argument(
        "--exec-max",
        type=float,
        default=6.0,
        help="Maximum notional in USD for actual executions (default: 6.0).",
    )

    return parser.parse_args()


def build_runtime_config_from_args(args: argparse.Namespace) -> RuntimeConfig:
    cfg = RuntimeConfig()
    cfg.engine_notional_usd = Decimal(str(args.engine_notional))
    cfg.exec_min_notional_usd = Decimal(str(args.exec_min))
    cfg.exec_max_notional_usd = Decimal(str(args.exec_max))
    return cfg


# ----------------------------------------------------------------------
# Helpers : construction signaux / requêtes
# ----------------------------------------------------------------------


def pick_exec_notional(cfg: RuntimeConfig) -> Decimal:
    """
    Choisit un notional d'exécution dans [exec_min, exec_max].
    Si la config est invalide, fallback sur engine_notional_usd.
    """
    try:
        lo = cfg.exec_min_notional_usd
        hi = cfg.exec_max_notional_usd
        if hi <= 0:
            raise ValueError("exec_max_notional_usd <= 0")
        if lo <= 0:
            lo = hi
        if lo == hi:
            return lo
        val = random.uniform(float(lo), float(hi))
        return Decimal(str(round(val, 2)))
    except Exception:
        return cfg.engine_notional_usd


def build_candidates(cfg: RuntimeConfig) -> List[MemecoinCandidate]:
    """
    On garde un gros notional pour la STRATÉGIE (200$) pour passer ses filtres,
    mais on exécutera derrière des tailles 2–6$.
    """
    return [
        MemecoinCandidate(
            symbol=cfg.symbol,
            chain="SOL",  # côté stratégie memecoin
            score=0.90,
            notional_usd=cfg.engine_notional_usd,
            wallet_id=cfg.wallet_id,
            meta={"debug": "paper_test_high_score"},
        ),
        # On pourrait ajouter d'autres candidats plus faibles si besoin
    ]


def signals_to_requests(
    cfg: RuntimeConfig,
    signals: List[Any],
) -> List[PaperExecutionRequest]:
    reqs: List[PaperExecutionRequest] = []

    for s in signals:
        side_raw = getattr(s, "side", None)
        if side_raw is None:
            continue
        side_str = getattr(side_raw, "value", str(side_raw)).lower()

        if side_str in ("buy", "long"):
            side = TradeSide.BUY
        elif side_str in ("sell", "short"):
            side = TradeSide.SELL
        else:
            continue

        exec_notional = pick_exec_notional(cfg)
        if exec_notional <= 0:
            continue

        symbol = getattr(s, "symbol", cfg.symbol)
        wallet_id = getattr(s, "wallet_id", cfg.wallet_id)
        meta = getattr(s, "meta", {}) or {}

        req = PaperExecutionRequest(
            chain=cfg.chain,
            symbol=symbol,
            side=side,
            notional_usd=exec_notional,
            wallet_id=wallet_id,
            strategy_tag="memecoin_farming",
            meta=meta,
        )
        reqs.append(req)

    return reqs


# ----------------------------------------------------------------------
# Main test
# ----------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("test_memecoin_paper_trades_sol")

    args = parse_args()
    cfg = build_runtime_config_from_args(args)

    logger.info("=== TEST_MEMECOIN_PAPER_TRADES_SOL (LIVE_150-like) ===")
    logger.info(
        "RuntimeConfig: symbol=%s chain=%s wallet=%s engine_notional=%.2f exec_range=%.2f-%.2f",
        cfg.symbol,
        cfg.chain,
        cfg.wallet_id,
        float(cfg.engine_notional_usd),
        float(cfg.exec_min_notional_usd),
        float(cfg.exec_max_notional_usd),
    )

    # ------------------------------------------------------------------
    # 1) Construction du moteur memecoin
    # ------------------------------------------------------------------
    # IMPORTANT : on revient à l'appel d'origine, sans argument.
    pair_cfgs = make_default_pair_configs()
    engine = MemecoinStrategyEngine(pair_configs=pair_cfgs, logger_=logger)

    logger.info(
        "MemecoinStrategyEngine initialisé avec %d pairs : %s",
        len(pair_cfgs),
        [p.symbol for p in pair_cfgs],
    )

    candidates = build_candidates(cfg)
    logger.info("Feed de %d candidats dans le moteur memecoin.", len(candidates))
    engine.feed_candidates(candidates)

    signals = engine.generate_signals()
    if not signals:
        logger.info(
            "generate_signals() — aucun signal généré à partir de %d candidats.",
            len(candidates),
        )
        logger.info("Aucun signal memecoin, rien à exécuter en PAPER.")
        return

    logger.info(
        "generate_signals() — %d signaux générés.",
        len(signals),
    )
    logger.info("Signals générés :")
    for s in signals:
        logger.info(
            "- id=%s wallet=%s symbol=%s side=%s notional=%s meta=%s",
            getattr(s, "id", None),
            getattr(s, "wallet_id", None),
            getattr(s, "symbol", None),
            getattr(getattr(s, "side", None), "value", getattr(s, "side", None)),
            getattr(s, "notional_usd", None),
            getattr(s, "meta", None),
        )

    # ------------------------------------------------------------------
    # 2) Execution PAPER + RISK LIVE_150 (RuntimeWalletManager + RuntimeWalletStats)
    # ------------------------------------------------------------------

    # 1) Charge config.json pour récupérer le profil risk + capital
    config_path = Path(ROOT_DIR) / "config.json"
    with config_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    finance_cfg = raw_cfg.get("finance", {})
    capital_usd = float(finance_cfg.get("capital_usd", 150.0))
    logger.info(
        "Config finance LIVE_150: profile=%s | capital_usd=%.2f",
        finance_cfg.get("profile"),
        capital_usd,
    )

    # 2) RuntimeWalletManager : métriques réelles (equity, PnL, etc.)
    wallet_logger = logger.getChild("wallets")
    runtime_wallet_manager = RuntimeWalletManager.from_config(
        raw_cfg,
        logger=wallet_logger,
    )

    # 3) RiskEngine LIVE_150
    risk_cfg = RiskConfig.from_dict(raw_cfg.get("risk", {}) or {})
    risk_engine = RiskEngine(config=risk_cfg)
    risk_engine.set_wallet_metrics(runtime_wallet_manager)

    # 4) Stats runtime pour ExecutionRiskAdapter
    stats = RuntimeWalletStats(wallet_manager=runtime_wallet_manager)

    # 5) ExecutionEngine papier de base (branché sur RuntimeWalletManager)
    pt_cfg = PaperTraderConfig.from_env()
    paper_trader = PaperTrader(config=pt_cfg)
    base_engine = PaperExecutionEngine(
        paper_trader=paper_trader,
        wallet_manager=runtime_wallet_manager,
        run_mode="paper",
    )

    # 6) Adapter Risk -> Execution
    exec_engine = ExecutionRiskAdapter(
        inner_engine=base_engine,
        risk_engine=risk_engine,
        stats_provider=stats,
        enabled=True,
    )

    logger.info(
        "PaperExecutionEngine+Risk initialisé (path=%s, max_trades=%d)",
        pt_cfg.path,
        pt_cfg.max_trades,
    )

    # 7) Exécution des requêtes
    reqs = signals_to_requests(cfg, signals)
    logger.info("Requêtes PAPER à exécuter : %d", len(reqs))

    for req in reqs:
        logger.info(
            "EXEC PAPER+RISK: %s %s %s USD sur %s (wallet=%s, tag=%s)",
            req.side.value.upper(),
            req.symbol,
            str(req.notional_usd),
            req.chain,
            req.wallet_id,
            req.strategy_tag,
        )
        exec_engine.execute(req)

    logger.info(
        "Trades écrits dans %s (lisibles via /godmode/trades et le dashboard).",
        pt_cfg.path,
    )
    logger.info("=== FIN TEST_MEMECOIN_PAPER_TRADES_SOL ===")


if __name__ == "__main__":
    main()
