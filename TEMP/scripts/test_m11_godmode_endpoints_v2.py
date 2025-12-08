#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Any

# --- Préparation du sys.path (même pattern que les autres scripts M10) ---
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.core.logging import get_logger  # type: ignore

log = get_logger("test_m11_godmode_endpoints_v2")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Test des endpoints GODMODE backend (status, wallets, trades, execution). "
            "À lancer pendant que start_godmode_m10_v2.py tourne."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8001",
        help="Base URL du backend GODMODE (défaut: http://127.0.0.1:8001).",
    )
    args = parser.parse_args()

    try:
        import requests  # type: ignore
    except ImportError:
        log.error(
            "Le module 'requests' n'est pas installé. "
            "Installe-le avec: pip install requests"
        )
        raise SystemExit(1)

    base_url: str = args.base_url.rstrip("/")
    endpoints = {
        "status": "/godmode/status",
        "wallets_runtime": "/godmode/wallets/runtime",
        "trades_runtime": "/godmode/trades/runtime",
        "execution_runtime": "/godmode/execution/runtime",
    }

    log.info("=== M11 – test_m11_godmode_endpoints_v2 ===")
    log.info("Base URL: %s", base_url)

    ok = True

    for name, path in endpoints.items():
        url = f"{base_url}{path}"
        log.info("Test endpoint %s: GET %s", name, url)

        try:
            resp = requests.get(url, timeout=5.0)
        except Exception as exc:
            log.error("ECHEC requête %s: %s", url, exc)
            ok = False
            continue

        log.info("Status HTTP %s: %d", name, resp.status_code)

        if resp.status_code != 200:
            log.error("Endpoint %s a retourné %d (attendu 200).", name, resp.status_code)
            ok = False
            continue

        # On essaie de parser le JSON pour vérifier que c'est bien du JSON valide
        try:
            data: Dict[str, Any] = resp.json()  # type: ignore[assignment]
        except Exception as exc:
            log.error(
                "Endpoint %s ne renvoie pas un JSON valide: %s. Réponse brute: %r",
                name,
                exc,
                resp.text[:200],
            )
            ok = False
            continue

        # On log juste les clés top-level pour debug, sans imposer un schéma strict
        if isinstance(data, dict):
            keys = list(data.keys())
            log.info("Endpoint %s – clés top-level: %s", name, keys)
        else:
            log.warning(
                "Endpoint %s a renvoyé un JSON non-dict (type=%r): %r",
                name,
                type(data),
                str(data)[:200],
            )

    if not ok:
        log.error("Un ou plusieurs endpoints GODMODE ont échoué.")
        raise SystemExit(1)

    log.info("Tous les endpoints GODMODE testés ont répondu 200 avec JSON valide.")
    log.info("test_m11_godmode_endpoints_v2 terminé.")


if __name__ == "__main__":
    main()

