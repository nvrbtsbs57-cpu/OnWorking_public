#!/usr/bin/env python
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Union

# ---------------------------------------------------------------------
# Bootstrapping du projet (PYTHONPATH = racine repo)
# ---------------------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# ---------------------------------------------------------------------
# Imports projet
# ---------------------------------------------------------------------

from bot.core.logging import get_logger
from bot.agent.engine import AgentEngine, AgentEngineConfig
from bot.trading.execution import ExecutionEngine
from bot.trading.paper_trader import PaperTrader, PaperTraderConfig
from bot.wallets.runtime_manager import RuntimeWalletManager

logger = get_logger(__name__)

TRADES_PATH = ROOT_DIR / "data" / "godmode" / "trades.jsonl"
WALLETS_RUNTIME_PATH = ROOT_DIR / "data" / "godmode" / "wallets_runtime.json"


# =====================================================================
# Helpers config
# =====================================================================


def load_config(config_path: Path | None = None) -> Union[Dict[str, Any], List[Any]]:
    """
    Charge un fichier JSON de config.
    - Peut renvoyer un dict ou une liste (selon ton config.json).
    - Si non trouvé → {}.
    """
    if config_path is None:
        config_path = ROOT_DIR / "config.json"

    if not config_path.exists():
        logger.warning(
            "Config non trouvée (%s). "
            "RuntimeWalletManager.from_config() sera appelée avec {}.",
            str(config_path),
        )
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        logger.info(
            "Config chargée depuis %s (type=%s)",
            str(config_path),
            type(cfg).__name__,
        )
        return cfg
    except Exception:
        logger.exception("Impossible de charger la config JSON (%s)", str(config_path))
        return {}


def _ensure_dict_for_subconfig(raw_cfg: Union[Dict[str, Any], List[Any]]) -> Dict[str, Any]:
    """
    Normalise la config pour qu'on ait toujours un dict :
    - si c'est déjà un dict → on le renvoie tel quel
    - si c'est une liste → on prend le premier élément si c'est un dict
    - sinon → {}
    """
    if isinstance(raw_cfg, dict):
        return raw_cfg
    if isinstance(raw_cfg, list) and raw_cfg:
        first = raw_cfg[0]
        if isinstance(first, dict):
            return first
    return {}


# =====================================================================
# Construction des moteurs
# =====================================================================


def build_runtime_wallet_manager(raw_cfg: Union[Dict[str, Any], List[Any]]) -> RuntimeWalletManager:
    """
    Construit un RuntimeWalletManager à partir de la config.
    """
    cfg_dict = _ensure_dict_for_subconfig(raw_cfg)
    rwm = RuntimeWalletManager.from_config(cfg_dict, logger=logger)
    logger.info("RuntimeWalletManager construit (snapshot initial écrit).")
    return rwm


def build_execution_engine(wallet_manager: RuntimeWalletManager) -> ExecutionEngine:
    """
    Construit le ExecutionEngine utilisé pour le PAPER **dans ce test**.

    On utilise le vrai moteur PaperTrader + ExecutionEngine(inner_engine, wallet_manager)
    comme dans le runtime, mais SANS envoyer de vraies transactions on-chain.
    """
    # Config PaperTrader depuis l'environnement (mêmes règles que le runtime)
    pt_cfg = PaperTraderConfig.from_env()
    paper_trader = PaperTrader(config=pt_cfg)

    # Wrapper haut-niveau avec propagation PnL → RuntimeWalletManager
    exec_engine = ExecutionEngine(
        inner_engine=paper_trader,
        wallet_manager=wallet_manager,
    )

    logger.info(
        "ExecutionEngine PAPER initialisé pour le test (wallet_manager=%s).",
        wallet_manager.__class__.__name__,
    )
    return exec_engine


def build_agent_engine(
    raw_cfg: Union[Dict[str, Any], List[Any]],
    paper_exec_engine: ExecutionEngine,
    runtime_wallet_manager: RuntimeWalletManager,
) -> AgentEngine:
    """
    Construit l'AgentEngine en mode PAPER, branché sur le moteur d'exécution
    + runtime_wallet_manager.
    """
    cfg_dict = _ensure_dict_for_subconfig(raw_cfg)
    agent_cfg_dict = cfg_dict.get("agent", {}) or {}
    agent_cfg = AgentEngineConfig.from_dict(agent_cfg_dict)

    agent = AgentEngine(
        config=agent_cfg,
        alert_engine=None,
        watchlist_wallet_manager=None,
        trade_wallet_manager=runtime_wallet_manager,
        execution_engine=paper_exec_engine,      # pour plus tard (LIVE éventuel)
        paper_execution_engine=paper_exec_engine,
        risk_engine=None,
    )

    logger.info(
        "AgentEngine construit (mode=%s, safety=%s).",
        agent.config.agent_mode,
        agent.config.safety_mode,
    )
    return agent


# =====================================================================
# Events de test → pipeline complet
# =====================================================================


def build_test_events() -> List[Dict[str, Any]]:
    """
    Construit 2 events de test pour copy_sol et sniper_sol.
    Ces events sont pensés pour être proches de ce que renverrait /godmode/events.
    """
    now = datetime.utcnow().isoformat()

    base_event: Dict[str, Any] = {
        "chain": "SOL",
        "symbol": "SOL/USDC",
        "notional_usd": "4.0",
        "price": "1.0",
        "event_type": "entry",
        "created_at": now,
    }

    ev_copy_buy = {
        **base_event,
        "side": "buy",
        "wallet_id": "copy_sol",
        "meta": {
            "strategy": "agent_test",
            "strategy_tag": "events:entry",
        },
    }

    ev_sniper_sell = {
        **base_event,
        "side": "sell",
        "wallet_id": "sniper_sol",
        "meta": {
            "strategy": "agent_test",
            "strategy_tag": "events:entry",
        },
    }

    return [ev_copy_buy, ev_sniper_sell]


# =====================================================================
# Debug helpers (trades.jsonl + wallets_runtime.json)
# =====================================================================


def tail_trades(path: Path, n: int = 20) -> None:
    """
    Affiche les N dernières lignes de trades.jsonl.
    (Ici, ce sont les trades habituels du runtime.)
    """
    if not path.exists():
        logger.warning("Fichier trades.jsonl inexistant : %s", str(path))
        return

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        logger.exception("Impossible de lire %s", str(path))
        return

    logger.info("===== Derniers trades (%d lignes) : %s =====", n, str(path))
    for line in lines[-n:]:
        print(line)


def show_wallets_runtime(path: Path) -> None:
    """
    Affiche un résumé de wallets_runtime.json (equity + nb wallets + PnL jour).
    """
    if not path.exists():
        logger.warning("wallets_runtime.json inexistant : %s", str(path))
        return

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        logger.exception("Impossible de lire/parsing wallets_runtime.json")
        return

    updated_at = data.get("updated_at")
    equity_total = data.get("equity_total_usd")
    wallets_count = data.get("wallets_count")
    pnl_day = data.get("pnl_day", {})

    logger.info("===== wallets_runtime.json (%s) =====", str(path))
    logger.info("updated_at       : %s", updated_at)
    logger.info("wallets_count    : %s", wallets_count)
    logger.info("equity_total_usd : %s", equity_total)
    logger.info("pnl_day          : %s", pnl_day)


# =====================================================================
# main
# =====================================================================


def main() -> None:
    # 1) Config (optionnel : on peut passer un chemin en argument)
    cfg_path = None
    if len(sys.argv) >= 2:
        cfg_path = Path(sys.argv[1])

    raw_cfg = load_config(cfg_path)

    # 2) RuntimeWalletManager
    runtime_wallet_manager = build_runtime_wallet_manager(raw_cfg)

    # 3) ExecutionEngine PAPER (PaperTrader + RuntimeWalletManager)
    exec_engine = build_execution_engine(runtime_wallet_manager)

    # 4) AgentEngine (events → Signals → exec_engine.execute_signal)
    agent = build_agent_engine(raw_cfg, exec_engine, runtime_wallet_manager)

    # 5) Events de test
    events = build_test_events()
    logger.info(
        "Envoi de %d events de test dans AgentEngine._process_events().",
        len(events),
    )

    # ⚠️ On appelle directement la méthode interne de traitement (pas run_forever)
    agent._process_events(events)  # type: ignore[attr-defined]

    # 6) Debug : derniers trades + snapshot wallets
    tail_trades(TRADES_PATH, n=20)
    show_wallets_runtime(WALLETS_RUNTIME_PATH)


if __name__ == "__main__":
    main()

