from __future__ import annotations

from typing import Optional, Dict

from .models import Alert, AlertContext, AlertSeverity
from .engine import AlertEngine

__all__ = [
    "Alert",
    "AlertContext",
    "AlertSeverity",
    "AlertEngine",
    "init_alert_engine",
    "get_alert_engine",
]

_global_alert_engine: Optional[AlertEngine] = None


def init_alert_engine(config: Dict) -> AlertEngine:
    """
    Initialise l'AlertEngine global à partir du config.json.
    À appeler une seule fois dans start_bot.py.
    """
    global _global_alert_engine
    _global_alert_engine = AlertEngine.from_config(config)
    return _global_alert_engine


def get_alert_engine() -> AlertEngine:
    """
    Récupère l'AlertEngine global.
    À utiliser partout dans le code du bot.
    """
    if _global_alert_engine is None:
        raise RuntimeError("AlertEngine not initialized. Call init_alert_engine(config) first.")
    return _global_alert_engine
