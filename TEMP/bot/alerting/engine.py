# bot/alerting/engine.py
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional

from .channels import ConsoleAlertChannel, FileAlertChannel, TelegramAlertChannel, AlertChannel
from .models import Alert, AlertSeverity, AlertContext

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {
    AlertSeverity.INFO: 0,
    AlertSeverity.WARNING: 1,
    AlertSeverity.CRITICAL: 2,
}


class AlertEngine:
    """
    Moteur d'alertes centralisé.
    - Filtre par sévérité minimale
    - Route vers les canaux configurés
    - Garde un buffer en mémoire pour le dashboard
    """

    def __init__(
        self,
        min_severity: AlertSeverity = AlertSeverity.INFO,
        buffer_size: int = 500,
    ) -> None:
        self.min_severity = min_severity
        self.buffer: Deque[Alert] = deque(maxlen=buffer_size)
        self.channels: List[AlertChannel] = []
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(cls, cfg: Dict) -> "AlertEngine":
        alerts_cfg = cfg.get("alerts", {})
        min_severity_str = alerts_cfg.get("min_severity_for_alert", "info")
        min_severity = AlertSeverity(min_severity_str)

        engine = cls(min_severity=min_severity, buffer_size=alerts_cfg.get("buffer_size", 500))

        channels_cfg = alerts_cfg.get("channels", {})

        # console
        console_cfg = channels_cfg.get("console", {})
        if console_cfg.get("enabled", True):
            engine.register_channel(ConsoleAlertChannel())

        # file
        file_cfg = channels_cfg.get("file", {})
        if file_cfg.get("enabled", False):
            engine.register_channel(
                FileAlertChannel(
                    path=file_cfg.get("path", "data/alerts/alerts.log"),
                    max_bytes=file_cfg.get("max_bytes", 10_000_000),
                    backup_count=file_cfg.get("backup_count", 5),
                )
            )

        # telegram
        telegram_cfg = channels_cfg.get("telegram", {})
        if telegram_cfg.get("enabled", False):
            engine.register_channel(
                TelegramAlertChannel(
                    bot_token=telegram_cfg.get("bot_token", ""),
                    chat_id=telegram_cfg.get("chat_id", ""),
                )
            )

        return engine

    def register_channel(self, channel: AlertChannel) -> None:
        self.channels.append(channel)

    def _should_alert(self, severity: AlertSeverity) -> bool:
        return SEVERITY_ORDER[severity] >= SEVERITY_ORDER[self.min_severity]

    async def emit(
        self,
        severity: AlertSeverity,
        title: str,
        message: str,
        source: str,
        context: Optional[AlertContext] = None,
    ) -> Alert:
        alert = Alert(
            id=str(uuid.uuid4()),
            time=datetime.utcnow(),
            severity=severity,
            title=title,
            message=message,
            source=source,
            context=context or AlertContext(),
        )

        if not self._should_alert(severity):
            return alert

        async with self._lock:
            self.buffer.appendleft(alert)  # plus récent en premier

        # envoi async aux canaux (non bloquant pour l’app)
        await asyncio.gather(*(ch.send(alert) for ch in self.channels), return_exceptions=True)

        return alert

    async def emit_info(self, title: str, message: str, source: str, context: Optional[AlertContext] = None) -> Alert:
        return await self.emit(AlertSeverity.INFO, title, message, source, context)

    async def emit_warning(self, title: str, message: str, source: str, context: Optional[AlertContext] = None) -> Alert:
        return await self.emit(AlertSeverity.WARNING, title, message, source, context)

    async def emit_critical(self, title: str, message: str, source: str, context: Optional[AlertContext] = None) -> Alert:
        return await self.emit(AlertSeverity.CRITICAL, title, message, source, context)

    def get_recent_alerts(self, limit: int = 100) -> List[Alert]:
        return list(list(self.buffer)[:limit])
