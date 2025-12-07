#!/usr/bin/env python
from __future__ import annotations

import json
import time
import sys
from pathlib import Path
from typing import Any, Dict, List

# ============================================================================
# Bootstrap chemin projet (IMPORTANT : avant tout import "bot.*")
# ============================================================================

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ============================================================================
# Imports projet
# ============================================================================

from bot.core.logging import get_logger
from bot.agent.engine import AgentEngine, AgentEngineConfig
from bot.trading.paper_trader import PaperTraderConfig

logger = get_logger(__name__)


def build_test_agent_engine() -> tuple[AgentEngine, PaperTraderConfig]:
    """
    Construit un AgentEngine configuré pour le PAPER à partir de l'env.

    ⚠️ IMPORTANT :
    - On NE construit PAS ExecutionEngine nous-même (pas de devinette de signature).
    - On laisse AgentEngine auto-initialiser le moteur PAPER (PaperExecutionEngine),
      comme prévu dans bot/agent/engine.py.
    """

    # Config "agent" minimaliste pour le test
    cfg_dict: Dict[str, Any] = {
        "agent_mode": "paper",
        "safety_mode": "SAFE",
        "events": {
            "url": "",
            "poll_interval_seconds": 1.0,
            "timeout_seconds": 10.0,
        },
        "events_backoff": {
            "max_consecutive_failures": 10,
            "initial_delay_seconds": 1.0,
            "max_delay_seconds": 60.0,
        },
        "execution": {
            "enabled": False,
        },
        "min_notional_usd": "0",
        "per_market_notional_usd": {},
        "allowed_event_types": ["entry", "signal"],
        "max_trades_per_minute_global": 0,
        "max_trades_per_minute_per_market": 0,
    }

    agent_cfg = AgentEngineConfig.from_dict(cfg_dict)

    # On lit la config PaperTrader pour récupérer le chemin des trades JSONL
    pt_cfg = PaperTraderConfig.from_env()

    # Très important : on ne passe PAS de moteur papier, on laisse AgentEngine
    # auto-initialiser PaperExecutionEngine en interne.
    agent = AgentEngine(
        config=agent_cfg,
        paper_execution_engine=None,
    )

    logger.info(
        "Test AgentEngine construit (mode=%s, PaperTrader path=%s, max_trades=%s)",
        agent_cfg.agent_mode,
        getattr(pt_cfg, "path", "?"),
        getattr(pt_cfg, "max_trades", "?"),
    )
    return agent, pt_cfg


def make_sample_events() -> List[Dict[str, Any]]:
    """
    Construit une petite liste d'events de test.
    """

    events: List[Dict[str, Any]] = []

    # Event 1 : BUY sur SOL/USDC, wallet copy_sol
    events.append(
        {
            "chain": "solana",
            "symbol": "SOL/USDC",
            "side": "buy",
            "notional_usd": "4.0",
            "type": "entry",
            "wallet_id": "copy_sol",
            "meta": {
                "test_case": "basic_buy_copy_sol",
            },
        }
    )

    # Event 2 : BUY sur SOL/USDC, wallet sniper_sol
    events.append(
        {
            "chain": "solana",
            "symbol": "SOL/USDC",
            "side": "buy",
            "notional_usd": "5.0",
            "type": "entry",
            "wallet_id": "sniper_sol",
            "meta": {
                "test_case": "basic_buy_sniper_sol",
            },
        }
    )

    return events


def print_trades_tail(trades_path: Path, max_lines: int = 20) -> None:
    """
    Affiche la fin du fichier de trades JSONL (si présent).
    """
    if not trades_path.is_file():
        logger.warning("Fichier trades.jsonl introuvable: %s", trades_path)
        return

    logger.info("Lecture des %d dernières lignes de %s", max_lines, trades_path)

    try:
        text = trades_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.exception("Impossible de lire %s: %s", trades_path, exc)
        return

    lines = [ln for ln in text.splitlines() if ln.strip()]
    tail = lines[-max_lines:] if len(lines) > max_lines else lines

    for ln in tail:
        try:
            obj = json.loads(ln)
        except Exception:
            print(ln)
            continue

        meta = obj.get("meta", {}) or {}
        wallet_id = meta.get("wallet_id")
        print(
            f"[{obj.get('created_at')}] {obj.get('chain')} {obj.get('symbol')} "
            f"{obj.get('side')} notional={obj.get('notional')} wallet_id={wallet_id}"
        )


def main() -> None:
    logger.info("=== TEST: AgentEngine events -> PAPER ===")

    agent, pt_cfg = build_test_agent_engine()

    events = make_sample_events()
    logger.info("Envoi de %d events à AgentEngine._process_events() ...", len(events))

    # On évite run_forever() pour ce test, on appelle directement la méthode interne.
    agent._process_events(events)

    # Petite pause si le moteur écrit sur disque
    time.sleep(0.5)

    trades_path_value = getattr(pt_cfg, "path", None)
    if not trades_path_value:
        logger.warning(
            "PaperTraderConfig.path est vide ou non défini, impossible de localiser trades.jsonl"
        )
        return

    trades_path = Path(trades_path_value)
    print_trades_tail(trades_path, max_lines=20)

    logger.info("=== FIN TEST: AgentEngine events -> PAPER ===")


if __name__ == "__main__":
    main()

