# bot/alerting/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertContext:
    chain: Optional[str] = None
    symbol: Optional[str] = None
    token_address: Optional[str] = None
    tx_hash: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Alert:
    id: str
    time: datetime
    severity: AlertSeverity
    title: str
    message: str
    source: str  # e.g. "AGENT", "INDEXER", "NORMALIZER", "WHALER"
    context: AlertContext = field(default_factory=AlertContext)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "time": self.time.isoformat(),
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "source": self.source,
            "context": {
                "chain": self.context.chain,
                "symbol": self.context.symbol,
                "token_address": self.context.token_address,
                "tx_hash": self.context.tx_hash,
                "extra": self.context.extra,
            },
        }
