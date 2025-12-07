from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import FastAPI, Query
from pydantic import BaseModel

from bot.alerting.engine import AlertEngine
from bot.alerting.models import AlertSeverity, AlertContext
from bot.api import godmode_dashboard  # <-- NEW: routes /godmode/trades|pnl|status

logger = logging.getLogger(__name__)


class AlertDTO(BaseModel):
    id: str
    time: str
    severity: str
    title: str
    message: str
    source: str
    context: Dict[str, Any]


class HealthDTO(BaseModel):
    status: str
    message: str


class AlertEmitRequest(BaseModel):
    severity: AlertSeverity
    title: str
    message: str
    source: str
    chain: str | None = None
    symbol: str | None = None
    token_address: str | None = None
    tx_hash: str | None = None
    extra: Dict[str, Any] = {}


def get_alert_engine(app: FastAPI) -> AlertEngine:
    engine = app.state.alert_engine
    if not isinstance(engine, AlertEngine):
        raise RuntimeError("AlertEngine not initialized in app.state")
    return engine


def create_app(alert_engine: AlertEngine) -> FastAPI:
    app = FastAPI(title="BOT GODMODE Dashboard", version="1.0.0")

    app.state.alert_engine = alert_engine

    # ------------------------ HEALTH ------------------------
    @app.get("/health", response_model=HealthDTO)
    async def health() -> HealthDTO:
        return HealthDTO(status="ok", message="Dashboard running in GODMODE")

    # ------------------------ ALERTS ------------------------
    @app.get("/alerts/recent", response_model=List[AlertDTO])
    async def get_recent_alerts(
        limit: int = Query(100, ge=1, le=500),
        severity: AlertSeverity | None = Query(None),
    ) -> List[AlertDTO]:
        engine = get_alert_engine(app)
        alerts = engine.get_recent_alerts(limit=limit)

        if severity is not None:
            alerts = [a for a in alerts if a.severity == severity]

        return [
            AlertDTO(
                id=a.id,
                time=a.time.isoformat(),
                severity=a.severity.value,
                title=a.title,
                message=a.message,
                source=a.source,
                context=a.to_dict()["context"],
            )
            for a in alerts
        ]

    @app.post("/alerts/emit-test", response_model=AlertDTO)
    async def emit_test_alert(payload: AlertEmitRequest) -> AlertDTO:
        engine = get_alert_engine(app)

        ctx = AlertContext(
            chain=payload.chain,
            symbol=payload.symbol,
            token_address=payload.token_address,
            tx_hash=payload.tx_hash,
            extra=payload.extra,
        )

        alert = await engine.emit(
            severity=payload.severity,
            title=payload.title,
            message=payload.message,
            source=payload.source,
            context=ctx,
        )

        return AlertDTO(
            id=alert.id,
            time=alert.time.isoformat(),
            severity=alert.severity.value,
            title=alert.title,
            message=alert.message,
            source=alert.source,
            context=alert.to_dict()["context"],
        )

    # ------------------------ GODMODE (trades / pnl / status) ------------------------
    # On branche ici le router défini dans bot/api/godmode_dashboard.py
    app.include_router(godmode_dashboard.router)

    return app
