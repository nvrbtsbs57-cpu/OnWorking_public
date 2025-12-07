# bot/api/alerts.py

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from bot.alerts.engine import AlertSeverity  # tu l'as déjà

router = APIRouter(
    prefix="/alerts",
    tags=["alerts"],
)

# Doit être aligné avec ta config alerts.channels.file.path
ALERTS_LOG_PATH = os.getenv("BOT_ALERTS_LOG_PATH", "data/alerts/alerts.log")


def _load_all_alert_records(path: str) -> List[Dict[str, Any]]:
    """
    Lit le fichier JSONL d'alertes et renvoie une liste de dicts.

    Chaque ligne ressemble à :
      {
        "time": "...",
        "severity": "info",
        "source": "bot.xxx",
        "msg": "Message de log",
        "extra": {...}
      }
    """
    if not os.path.exists(path):
        return []

    records: List[Dict[str, Any]] = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                # si une ligne est corrompue, on l'ignore
                continue
            records.append(data)

    return records


@router.get("/recent")
def get_recent_alerts(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = Query(
        None,
        description="Filtre optionnel : info, warning, error, critical…",
    ),
    source: Optional[str] = Query(
        None,
        description="Filtre optionnel : nom du logger/source (ex: runtime, api.godmode)",
    ),
) -> List[Dict[str, Any]]:
    """
    Renvoie les dernières alertes enregistrées par AlertEngine (fichier JSONL).

    Format de sortie (pensé pour le dashboard) :

    [
      {
        "time": "...",
        "severity": "info",
        "source": "bot.runtime",
        "message": "Message complet",
        "extra": { ... },
      },
      ...
    ]
    """
    try:
        records = _load_all_alert_records(ALERTS_LOG_PATH)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=500,
            detail=f"Impossible de lire le fichier d'alertes: {exc}",
        )

    if not records:
        return []

    # filtrage par sévérité
    if severity:
        sev = AlertSeverity.from_str(severity)
        sev_value = sev.value  # "info", "warning", etc.
        records = [r for r in records if r.get("severity") == sev_value]

    # filtrage par source (exact, insensible à la casse)
    if source:
        source_lower = source.lower()
        records = [
            r
            for r in records
            if str(r.get("source", "")).lower() == source_lower
        ]

    # on prend les N dernières et on renvoie dans l'ordre "plus récent d'abord"
    records = records[-limit:]
    records.reverse()

    # On adapte la structure au front : "msg" -> "message"
    return [
        {
            "time": r.get("time"),
            "severity": r.get("severity"),
            "source": r.get("source"),
            "message": r.get("msg"),
            "extra": r.get("extra") or {},
        }
        for r in records
    ]
