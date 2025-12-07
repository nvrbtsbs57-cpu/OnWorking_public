from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from bot.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Modèle d'alerte
# ---------------------------------------------------------------------------


class AlertSeverity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @classmethod
    def from_str(cls, value: str) -> "AlertSeverity":
        v = str(value).lower()
        if v == "warn":
            v = "warning"
        if v == "fatal":
            v = "critical"
        for m in cls:
            if m.value == v:
                return m
        return cls.INFO


_LEVEL_NUM = {
    AlertSeverity.DEBUG: 10,
    AlertSeverity.INFO: 20,
    AlertSeverity.WARNING: 30,
    AlertSeverity.ERROR: 40,
    AlertSeverity.CRITICAL: 50,
}


@dataclass
class AlertRecord:
    time: str
    severity: AlertSeverity
    source: str
    msg: str
    extra: Dict[str, Any]

    def to_json(self) -> str:
        data = asdict(self)
        data["severity"] = self.severity.value
        return json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class AlertBackend:
    def emit(self, record: AlertRecord) -> None:
        raise NotImplementedError


class FileAlertBackend(AlertBackend):
    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def emit(self, record: AlertRecord) -> None:
        line = record.to_json()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class NullAlertBackend(AlertBackend):
    def emit(self, record: AlertRecord) -> None:  # pragma: no cover
        # Pas d'output (utile si on désactive les alertes)
        pass


# ---------------------------------------------------------------------------
# Logging bridge handler
# ---------------------------------------------------------------------------


class _LoggingBridgeHandler(logging.Handler):
    """
    Handler logging qui envoie les logs vers AlertEngine.
    """

    def __init__(self, engine: "AlertEngine") -> None:
        super().__init__()
        self.engine = engine

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Convertit le niveau logging (INFO, WARNING, etc.) en AlertSeverity
            sev = AlertSeverity.from_str(record.levelname)

            if not self.engine._should_emit(sev):
                return

            # message formaté
            msg = self.format(record)

            # source = nom du logger
            source = record.name

            # on extrait quelques extras intéressants
            extras: Dict[str, Any] = {}
            for key, value in record.__dict__.items():
                if key in {
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                }:
                    continue
                extras[key] = value

            self.engine.emit(sev, msg, source=source, **extras)
        except Exception:
            # sécurité: on ne relance pas d'exception depuis un handler logging
            pass


# ---------------------------------------------------------------------------
# AlertEngine
# ---------------------------------------------------------------------------


class AlertEngine:
    """
    Moteur d'alertes simple.

    - Écrit les alertes dans un fichier JSONL (data/alerts/alerts.log par défaut)
    - Peut être utilisé directement: engine.emit("info", "msg", foo=123)
    - Installe un handler logging pour transformer les logs en alertes.
    """

    def __init__(self, backend: AlertBackend, min_level: AlertSeverity) -> None:
        self.backend = backend
        self.min_level = min_level
        self._logging_bridge_installed = False

        logger.info(
            "AlertEngine backend=%s, path=%s, min_level=%s",
            type(backend).__name__,
            getattr(backend, "path", None),
            self.min_level.value,
        )

    # ----------------- API publique -----------------

    def emit(
        self,
        severity: AlertSeverity | str,
        msg: str,
        *,
        source: Optional[str] = None,
        **extra: Any,
    ) -> None:
        if isinstance(severity, AlertSeverity):
            sev = severity
        else:
            sev = AlertSeverity.from_str(str(severity))

        if not self._should_emit(sev):
            return

        record = AlertRecord(
            time=datetime.utcnow().isoformat(),
            severity=sev,
            source=source or "bot",
            msg=msg,
            extra=extra,
        )

        try:
            self.backend.emit(record)
        except Exception:
            # on évite toute boucle infinie
            pass

    # helpers
    def debug(self, msg: str, **extra: Any) -> None:
        self.emit(AlertSeverity.DEBUG, msg, **extra)

    def info(self, msg: str, **extra: Any) -> None:
        self.emit(AlertSeverity.INFO, msg, **extra)

    def warning(self, msg: str, **extra: Any) -> None:
        self.emit(AlertSeverity.WARNING, msg, **extra)

    def error(self, msg: str, **extra: Any) -> None:
        self.emit(AlertSeverity.ERROR, msg, **extra)

    # ----------------- Logging bridge -----------------

    def _should_emit(self, severity: AlertSeverity) -> bool:
        return _LEVEL_NUM[severity] >= _LEVEL_NUM[self.min_level]

    def install_logging_bridge(self) -> None:
        """
        Ajoute un handler logging qui envoie *tous* les logs
        (à partir de min_level) vers AlertEngine.
        """
        if self._logging_bridge_installed:
            return

        handler = _LoggingBridgeHandler(self)
        handler.setLevel(logging.DEBUG)  # on filtre avec min_level nous-mêmes

        root = logging.getLogger()  # root logger
        root.addHandler(handler)

        self._logging_bridge_installed = True
        logger.info("AlertEngine logging bridge installé sur le root logger.")

    # ----------------- Config helper -----------------

    @classmethod
    def from_config(cls, cfg: Any) -> Optional["AlertEngine"]:
        """
        cfg = bloc "alerts" de ton config.json :

        {
          "enabled": true,
          "min_severity_for_alert": "info",
          "channels": {
            "console": { "enabled": true },
            "file": {
              "enabled": true,
              "path": "data/alerts/alerts.log"
            }
          }
        }
        """

        def _get(obj: Any, key: str, default: Any = None) -> Any:
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        enabled = bool(_get(cfg, "enabled", True))
        if not enabled:
            logger.info("AlertEngine désactivé via la config.")
            return None

        min_level_str = _get(cfg, "min_severity_for_alert", "info")
        min_level = AlertSeverity.from_str(str(min_level_str))

        channels = _get(cfg, "channels", {}) or {}
        file_cfg = _get(channels, "file", {}) or {}
        file_enabled = bool(_get(file_cfg, "enabled", True))
        path = _get(file_cfg, "path", "data/alerts/alerts.log")

        if file_enabled:
            backend: AlertBackend = FileAlertBackend(path)
            backend_name = "file"
        else:
            backend = NullAlertBackend()
            backend_name = "null"

        engine = cls(backend=backend, min_level=min_level)
        engine.install_logging_bridge()

        logger.info(
            "AlertEngine initialisé (enabled=%s, backend=%s, path=%s, min_level=%s)",
            enabled,
            backend_name,
            path,
            min_level.value,
        )

        return engine


def build_alert_engine(cfg: Any) -> Optional[AlertEngine]:
    return AlertEngine.from_config(cfg)
