from __future__ import annotations

from pathlib import Path
import threading
import logging
from typing import List

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from bot.api import godmode_dashboard
from bot.api.alerts_api import router as alerts_router
from bot.indexer.engine import IndexerEngine
from bot.normalizer.engine import NormalizerEngine
from bot.mapper.engine import EventMapper
from bot.agent.engine import AgentEngine
from bot.alerting.engine import AlertEngine

from bot.api.models import (
    HealthResponse,
    BlockResponse,
    TickResponse,
    EventResponse,
    SignalResponse,
)

logger = logging.getLogger(__name__)


class APIServer:
    """
    FASTAPI DASHBOARD — GODMODE
    --------------------------------------------------
    Expose un cockpit complet pour monitorer tout le bot.
    """

    def __init__(
        self,
        indexer: IndexerEngine,
        normalizer: NormalizerEngine,
        mapper: EventMapper,
        agent: AgentEngine,
        alerting: AlertEngine,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:

        self.indexer = indexer
        self.normalizer = normalizer
        self.mapper = mapper
        self.agent = agent
        self.alerting = alerting

        self.host = host
        self.port = port

        self.app = FastAPI(title="BOT GODMODE API")

        # --- Static files pour le dashboard (images, css, etc.) ---
        # server.py est dans .../BOT_GODMODE/bot/api/server.py
        # On cherche un dossier "static" soit à la racine, soit dans bot/static.
        here = Path(__file__).resolve()
        root_dir = here.parents[2]  # .../BOT_GODMODE
        candidates = [
            root_dir / "static",  # BOT_GODMODE/static
            root_dir / "bot" / "static",  # BOT_GODMODE/bot/static
        ]

        mounted = False
        for d in candidates:
            if d.exists():
                logger.info("Mounting static files from %s", d)
                self.app.mount(
                    "/static",
                    StaticFiles(directory=str(d)),
                    name="static",
                )
                mounted = True
                break

        if not mounted:
            logger.warning(
                "No static directory found. Tried: %s",
                ", ".join(str(c) for c in candidates),
            )

        self._register_routes()

    # -------------------------------------------------------
    # Start API in thread
    # -------------------------------------------------------

    def start_in_thread(self) -> threading.Thread:
        t = threading.Thread(
            target=lambda: uvicorn.run(
                self.app,
                host=self.host,
                port=self.port,
                log_level="info",
            ),
            daemon=True,
        )
        t.start()
        logger.info("API Dashboard running on http://%s:%d", self.host, self.port)
        return t

    # -------------------------------------------------------
    # ROUTES
    # -------------------------------------------------------

    def _register_routes(self) -> None:
        app = self.app

        # ------------------------ HEALTH ------------------------
        @app.get("/health", response_model=HealthResponse)
        def health():
            return HealthResponse(
                status="ok",
                indexer_running=self.indexer is not None,
                normalizer_running=self.normalizer is not None,
                mapper_running=self.mapper is not None,
                agent_running=self.agent is not None,
                alerting_running=self.alerting is not None,
            )

        # ------------------------ BLOCKS ------------------------
        @app.get("/blocks", response_model=List[BlockResponse])
        def get_blocks():
            results = []
            for chain, pos in self.indexer.last_positions.items():
                results.append(
                    BlockResponse(
                        chain=chain,
                        block_number=pos,
                        timestamp=0.0,
                        tx_count=0,
                    )
                )
            return results

        # ------------------------ TICKS ------------------------
        @app.get("/ticks", response_model=List[TickResponse])
        def get_ticks():
            ticks = list(self.normalizer.state.ticks)[-50:]
            return [
                TickResponse(
                    chain=t.chain,
                    block_number=t.block_number,
                    activity_level=t.activity_level,
                    volume_estimate=t.volume_estimate,
                    price_impact_estimate=t.price_impact_estimate,
                )
                for t in ticks
            ]

        # ------------------------ EVENTS ------------------------
        @app.get("/events", response_model=List[EventResponse])
        def get_events():
            events = list(self.mapper.state.events)[-50:]
            out = []
            for e in events:
                out.append(
                    EventResponse(
                        type=e.type,
                        chain=e.chain,
                        block_number=e.block_number,
                        severity=getattr(e, "severity", "NONE"),
                    )
                )
            return out

        # ------------------------ SIGNALS ------------------------
        @app.get("/signals", response_model=List[SignalResponse])
        def get_signals():
            last_sig = self.agent.state.last_signal
            if not last_sig:
                return []

            return [
                SignalResponse(
                    type=last_sig,
                    confidence=self.agent.state.risk_level,
                    reason="Generated by agent",
                )
            ]

        # ------------------------ GODMODE DASHBOARD ------------------------
        # Trades / PnL / Status GODMODE (papier) + finance / alerts
        app.include_router(godmode_dashboard.router)

        # ------------------------ ALERTS RECENTES ------------------------
        # /alerts/recent — utilisé par le dashboard HTML
        app.include_router(alerts_router)
