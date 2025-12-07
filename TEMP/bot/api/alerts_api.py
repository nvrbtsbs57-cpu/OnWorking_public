# bot/api/alerts_api.py
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/alerts", tags=["alerts"])

# même path que celui utilisé par FileAlertBackend
ALERTS_LOG_PATH = os.getenv("BOT_ALERTS_FILE", "data/alerts/alerts.log")

_SEVERITY_ORDER: Dict[str, int] = {
    "debug": 10,
    "info": 20,
    "warning": 30,
    "error": 40,
    "critical": 50,
}


class AlertItem(BaseModel):
    time: datetime
    severity: str
    source: str
    title: str
    msg: str
    extra: Dict[str, Any]


def _parse_time(value: Any) -> datetime:
    if not value:
        return datetime.utcnow()
    s = str(value)
    # compat iso "2025-11-27T12:34:56Z"
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.utcnow()


@router.get("/recent", response_model=List[AlertItem])
def get_recent_alerts(
    limit: int = Query(50, ge=1, le=500),
    min_severity: Optional[str] = Query(
        None,
        description="Filtre minimal de sévérité (debug|info|warning|error|critical)",
    ),
) -> List[AlertItem]:
    """
    Retourne les dernières alertes présentes dans data/alerts/alerts.log

    - triées par time DESC
    - possibilité de filtrer par sévérité mini
    """
    if not os.path.exists(ALERTS_LOG_PATH):
        # pas encore d'alertes → liste vide tranquille
        return []

    min_sev_num: Optional[int] = None
    if min_severity:
        sev_key = str(min_severity).lower()
        min_sev_num = _SEVERITY_ORDER.get(sev_key)
        if min_sev_num is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown severity '{min_severity}', attendu: "
                "debug|info|warning|error|critical",
            )

    # on lit toutes les lignes et on remonte à l'envers
    with open(ALERTS_LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()

    items: List[AlertItem] = []

    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        sev_str = str(data.get("severity", "info")).lower()
        sev_num = _SEVERITY_ORDER.get(sev_str, _SEVERITY_ORDER["info"])

        if min_sev_num is not None and sev_num < min_sev_num:
            continue

        extra = data.get("extra") or {}
        msg = data.get("msg") or ""
        title = (
            extra.get("title")
            or extra.get("message")
            or msg
            or "Alert"
        )

        item = AlertItem(
            time=_parse_time(data.get("time")),
            severity=sev_str,
            source=str(data.get("source") or "bot"),
            title=str(title)[:200],
            msg=str(msg),
            extra=extra,
        )
        items.append(item)

        if len(items) >= limit:
            break

    # on re-tri par time DESC au cas où
    items.sort(key=lambda a: a.time, reverse=True)
    return items
