# bot/alerting/channels.py
from __future__ import annotations

import json
import logging
import logging.handlers
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import httpx  # pip install httpx

from .models import Alert

logger = logging.getLogger(__name__)


class AlertChannel(ABC):
    @abstractmethod
    async def send(self, alert: Alert) -> None:
        ...


class ConsoleAlertChannel(AlertChannel):
    async def send(self, alert: Alert) -> None:
        data = alert.to_dict()
        logger.info("[ALERT][%s] %s — %s", data["severity"], data["title"], data["message"])


class FileAlertChannel(AlertChannel):
    def __init__(self, path: str, max_bytes: int = 10_000_000, backup_count: int = 5) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("alerts.file")
        self.logger.setLevel(logging.INFO)

        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in self.logger.handlers):
            handler = logging.handlers.RotatingFileHandler(
                self.path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
            fmt = logging.Formatter('%(message)s')
            handler.setFormatter(fmt)
            self.logger.addHandler(handler)

    async def send(self, alert: Alert) -> None:
        self.logger.info(json.dumps(alert.to_dict(), ensure_ascii=False))


class TelegramAlertChannel(AlertChannel):
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, alert: Alert) -> None:
        if not self.enabled:
            logger.debug("TelegramAlertChannel not configured, skipping")
            return

        text = f"⚠️ *{alert.severity.value.upper()}* — *{alert.title}*\n\n{alert.message}"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.warning(
                        "Telegram alert send failed: status=%s body=%s",
                        resp.status_code,
                        resp.text,
                    )
        except Exception:
            logger.exception("Error while sending Telegram alert")
