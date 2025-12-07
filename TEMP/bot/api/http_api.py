# bot/api/http_api.py
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from bot.api.query_engine import QueryEngine
from bot.bot_core.normalizer.normalizer_engine import NormalizerEngine
from bot.signals import events_to_signals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers de sérialisation
# ---------------------------------------------------------------------------

def _to_jsonable(obj: Any) -> Any:
    """
    Convertit dataclasses / Decimal / datetime en objets JSON-compatibles.
    """
    if is_dataclass(obj):
        obj = asdict(obj)

    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]

    if isinstance(obj, (Decimal, datetime)):
        return str(obj)

    return obj


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        _to_jsonable(payload),
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

def run_http_api(
    normalizer: NormalizerEngine,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> ThreadingHTTPServer:
    """
    Lance l'API HTTP interne (health, events, signals) dans un thread séparé
    et renvoie l'instance du serveur.

    Endpoints :
      - GET /health  -> {"status": "ok"}
      - GET /events  -> {"events": [...]}
      - GET /signals -> {"signals": [...]}
    """
    query_engine = QueryEngine(normalizer)

    class ApiHandler(BaseHTTPRequestHandler):
        # On force HTTP/1.1 pour rester propre côté client
        protocol_version = "HTTP/1.1"

        # On évite le spam stdout du BaseHTTPRequestHandler
        def log_message(self, format: str, *args: Any) -> None:  # type: ignore[override]
            logger.debug("HTTP %s - " + format, self.address_string(), *args)

        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # on ferme la connexion après la réponse (simple et robuste)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # type: ignore[override]
            path = self.path.split("?", 1)[0]

            if path == "/health":
                self._send_json({"status": "ok"})

            elif path == "/events":
                events = query_engine.get_recent_events()
                self._send_json({"events": events})

            elif path == "/signals":
                events = query_engine.get_recent_events()
                signals = events_to_signals(events)
                self._send_json({"signals": [s.to_dict() for s in signals]})

            else:
                self._send_json({"error": "not_found"}, status=404)

    server = ThreadingHTTPServer((host, port), ApiHandler)

    def _serve() -> None:
        logger.info("HTTP API listening on http://%s:%s", host, port)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            logger.info("HTTP API stopped")

    # On lance le serveur HTTP dans un thread dédié
    t = threading.Thread(target=_serve, name="http_api_server", daemon=True)
    t.start()

    return server
