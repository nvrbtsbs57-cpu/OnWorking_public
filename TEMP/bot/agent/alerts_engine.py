from typing import List

from bot.core.logging import get_logger

logger = get_logger(__name__)


class AlertsEngine:
    def dispatch(self, alerts: List[str]) -> None:
        for a in alerts:
            logger.info(f"[ALERT] {a}")
