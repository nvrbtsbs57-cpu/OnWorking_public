#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
scripts/inspect_events_to_requests.py

Inspecte ce que voit l'AgentEngine sur l'endpoint /events, sans exécuter de trades.

- charge config.json
- reconstruit l'URL des events (api.host/port + agent.events_path)
- construit un AgentEngineConfig avec les mêmes règles que start_bot.py
- appelle AgentEngine._fetch_events() une fois
- pour chaque event, affiche un résumé et ce que donnerait
  _build_paper_request_from_event (ExecutionRequest papier), sans l'envoyer
  au PaperExecutionEngine.

Usage :

    cd BOT_GODMODE
    python scripts/inspect_events_to_requests.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import sys
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from bot.agent.engine import AgentEngine, AgentEngineConfig  # type: ignore
from bot.core.logging import get_logger  # type: ignore

logger = get_logger(__name__)


def _get_section(cfg: Any, name: str, default: Any = None) -> Any:
    if isinstance(cfg, dict):
        return cfg.get(name, default)
    return getattr(cfg, name, default)


def _get_value(section: Any, name: str, default: Any = None) -> Any:
    if section is None:
        return default
    if isinstance(section, dict):
        return section.get(name, default)
    return getattr(section, name, default)


def build_agent_engine_for_inspect(config_path: Path) -> AgentEngine:
    """
    Construit un AgentEngine configuré comme dans start_bot.py,
    mais destiné à l'inspection (on n'appelle pas run_forever, ni _process_events).
    """
    with config_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    api_cfg = _get_section(raw_cfg, "api") or {}
    api_host = api_cfg.get("host", "127.0.0.1")
    api_port = int(api_cfg.get("port", 8000))

    agent_cfg = _get_section(raw_cfg, "agent") or {}
    events_path = _get_value(agent_cfg, "events_path", "/events") or "/events"
    events_path = str(events_path)
    if not events_path.startswith("/"):
        events_path = "/" + events_path

    api_base_url = f"http://{api_host}:{api_port}"
    events_url = f"{api_base_url}{events_path}"

    # Quelques champs de haut niveau simples
    agent_mode = "paper"   # en GODMODE/RUN_MODE=paper
    safety_mode = str(raw_cfg.get("SAFETY_MODE", "safe")).upper()

    # Lecture des règles PAPER depuis la section agent
    min_notional_raw = _get_value(agent_cfg, "min_notional_usd", "0")
    try:
        min_notional_usd = Decimal(str(min_notional_raw))
    except Exception:
        min_notional_usd = Decimal("0")

    per_market_notional_usd = _get_value(agent_cfg, "per_market_notional_usd", {}) or {}
    allowed_event_types = _get_value(agent_cfg, "allowed_event_types", None)
    max_trades_per_minute_global = int(
        _get_value(agent_cfg, "max_trades_per_minute_global", 0) or 0
    )
    max_trades_per_minute_per_market = int(
        _get_value(agent_cfg, "max_trades_per_minute_per_market", 0) or 0
    )

    cfg_dict: Dict[str, Any] = {
        "agent_mode": agent_mode,
        "safety_mode": safety_mode,
        "events": {
            "url": events_url,
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
        # Règles PAPER
        "min_notional_usd": str(min_notional_usd),
        "per_market_notional_usd": per_market_notional_usd,
        "allowed_event_types": allowed_event_types,
        "max_trades_per_minute_global": max_trades_per_minute_global,
        "max_trades_per_minute_per_market": max_trades_per_minute_per_market,
    }

    agent_engine_config = AgentEngineConfig.from_dict(cfg_dict)

    # On n'a pas besoin de WalletManager / RiskEngine / ExecutionEngine ici.
    engine = AgentEngine(
        config=agent_engine_config,
        alert_engine=None,
        watchlist_wallet_manager=None,
        trade_wallet_manager=None,
        execution_engine=None,
        paper_execution_engine=None,  # auto-init possible mais peu importe ici
        risk_engine=None,
    )

    logger.info(
        "AgentEngine d'inspection initialisé (mode=%s, safety=%s, events_url=%s)",
        agent_engine_config.agent_mode,
        agent_engine_config.safety_mode,
        agent_engine_config.events_url,
    )
    return engine


def main() -> None:
    config_path = BASE_DIR / "config.json"
    if not config_path.exists():
        print(f"config.json introuvable à {config_path}", file=sys.stderr)
        sys.exit(1)

    engine = build_agent_engine_for_inspect(config_path)

    # On appelle une seule fois _fetch_events()
    try:
        events: List[Dict[str, Any]] = engine._fetch_events()  # type: ignore[attr-defined]
    except Exception as exc:
        print(f"Erreur lors de l'appel à /events : {exc}")
        sys.exit(1)

    print()
    print(f"URL /events utilisée : {engine.config.events_url}")
    print(f"Nombre d'events récupérés : {len(events)}")

    if not events:
        print("Aucun event (vérifie que l'indexer / normalizer tourne).")
        return

    print("\nAperçu des events bruts (max 10) :")
    for ev in events[:10]:
        etype = ev.get("event_type") or ev.get("type") or ev.get("category") or "unknown"
        chain = ev.get("chain") or ev.get("network") or "unknown"
        symbol = ev.get("symbol") or ev.get("token_symbol") or ev.get("asset") or "?"
        notional = (
            ev.get("notional_usd")
            or ev.get("usd_notional")
            or ev.get("notional")
            or ev.get("amount_usd")
            or ev.get("value_usd")
        )
        side = ev.get("side") or ev.get("direction") or ev.get("order_side") or ""
        print(
            f"- type={etype} | chain={chain} | symbol={symbol} | "
            f"side={side} | notional_usd={notional}"
        )

    print("\nCe que donnerait _build_paper_request_from_event (max 10) :")
    built = 0
    skipped = 0
    for ev in events[:10]:
        req = engine._build_paper_request_from_event(ev)  # type: ignore[attr-defined]
        if req is None:
            skipped += 1
            print("  [IGNORÉ] Event filtré par les règles (voir logs).")
        else:
            built += 1
            print(
                f"  [OK] {req.side.value.upper()} {req.symbol} "
                f"notional={req.notional_usd} on {req.chain} "
                f"(tag={req.strategy_tag})"
            )

    print()
    print(f"Résumé : {built} requêtes papier construites, {skipped} events ignorés.")
    print("Les trades papier réels ne seront générés que par AgentEngine.run_forever().")


if __name__ == "__main__":
    main()
