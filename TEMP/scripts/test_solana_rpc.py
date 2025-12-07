#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Test de connectivité au RPC Solana.

Usage:
    python scripts/test_solana_rpc.py
    python scripts/test_solana_rpc.py --url https://mon-rpc-solana...

Le script:
  - charge config.json à la racine du projet,
  - récupère l'URL RPC Solana (chains[].name == "solana" ou indexer.chains.solana),
  - effectue quelques appels JSON-RPC (getHealth, getVersion, getSlot),
  - log en JSON sur stdout,
  - renvoie exit code 0 si tout est OK, 1 sinon.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


# ----------------------------------------------------------------------
# Chemins de base
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"


# ----------------------------------------------------------------------
# Logging JSON simple
# ----------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    def formatTime(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:  # type: ignore[override]
        dt = datetime.fromtimestamp(record.created)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat()

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload = {
            "time": self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S.%f"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


logger = logging.getLogger("test_solana_rpc")


# ----------------------------------------------------------------------
# Helpers config
# ----------------------------------------------------------------------


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        print(f"[FATAL] Fichier config.json introuvable: {path}", file=sys.stderr)
        sys.exit(1)
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_solana_rpc_in_config(cfg: Dict[str, Any]) -> Optional[str]:
    """
    Cherche l'URL RPC Solana dans:
      - top-level "chains": [{"name": "solana", "rpc_url": "..."}]
      - ou indexer.chains.solana.rpc_url
    """
    # 1) top-level chains[]
    chains = cfg.get("chains", [])
    if isinstance(chains, list):
        for c in chains:
            try:
                if c.get("name") == "solana" and c.get("rpc_url"):
                    return str(c["rpc_url"])
            except AttributeError:
                continue

    # 2) indexer.chains.solana.rpc_url
    indexer = cfg.get("indexer", {}) or {}
    indexer_chains = indexer.get("chains", {}) or {}
    sol_cfg = indexer_chains.get("solana") or {}
    if isinstance(sol_cfg, dict) and sol_cfg.get("rpc_url"):
        return str(sol_cfg["rpc_url"])

    return None


# ----------------------------------------------------------------------
# RPC client Solana minimal
# ----------------------------------------------------------------------


@dataclass
class RpcResult:
    ok: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    status_code: Optional[int] = None


def solana_rpc_call(
    url: str, method: str, params: Optional[Any] = None, timeout: float = 10.0
) -> RpcResult:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=timeout) as resp:  # type: ignore[arg-type]
            status = getattr(resp, "status", None)
            raw = resp.read().decode("utf-8")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                return RpcResult(
                    ok=False,
                    error=f"Réponse non-JSON: {exc} (raw={raw[:200]!r})",
                    status_code=status,
                )

            if "error" in data:
                return RpcResult(
                    ok=False,
                    data=data,
                    error=f"RPC error: {data['error']}",
                    status_code=status,
                )

            return RpcResult(ok=True, data=data, status_code=status)

    except HTTPError as exc:
        return RpcResult(
            ok=False,
            error=f"HTTPError: {exc} {getattr(exc, 'reason', '')}",
            status_code=exc.code,
        )
    except URLError as exc:
        return RpcResult(
            ok=False,
            error=f"URLError: {exc}",
            status_code=None,
        )
    except Exception as exc:
        return RpcResult(
            ok=False,
            error=f"Exception: {exc}",
            status_code=None,
        )


# ----------------------------------------------------------------------
# main()
# ----------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Test RPC Solana (BOT_GODMODE)")
    parser.add_argument(
        "--config",
        default=str(CONFIG_PATH),
        help="Chemin vers config.json (par défaut: %(default)s)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="URL RPC Solana (override, sinon prise depuis config.json)",
    )
    args = parser.parse_args()

    setup_logging(level="INFO")

    cfg_path = Path(args.config).resolve()
    cfg = load_config(cfg_path)
    logger.info("Config chargée depuis %s", cfg_path)

    if args.url:
        rpc_url = args.url
        logger.info("Override URL RPC Solana via CLI: %s", rpc_url)
    else:
        rpc_url = find_solana_rpc_in_config(cfg)
        if not rpc_url:
            logger.error(
                "Impossible de trouver l'URL RPC Solana dans la config "
                "(chains[].name == 'solana' ou indexer.chains.solana.rpc_url)."
            )
            sys.exit(1)
        logger.info("URL RPC Solana trouvée dans la config: %s", rpc_url)

    # 1) getHealth
    logger.info("Appel Solana RPC: getHealth")
    r_health = solana_rpc_call(rpc_url, "getHealth")
    if r_health.ok:
        logger.info("getHealth OK: %s", r_health.data)
    else:
        logger.error("getHealth FAILED: %s", r_health.error)

    # 2) getVersion
    logger.info("Appel Solana RPC: getVersion")
    r_version = solana_rpc_call(rpc_url, "getVersion")
    if r_version.ok:
        logger.info("getVersion OK: %s", r_version.data)
    else:
        logger.error("getVersion FAILED: %s", r_version.error)

    # 3) getSlot
    logger.info("Appel Solana RPC: getSlot")
    r_slot = solana_rpc_call(rpc_url, "getSlot")
    if r_slot.ok:
        logger.info("getSlot OK: %s", r_slot.data)
    else:
        logger.error("getSlot FAILED: %s", r_slot.error)

    # Résumé final
    all_ok = r_health.ok and r_version.ok and r_slot.ok
    if all_ok:
        logger.info(
            "TEST_SOLANA_RPC_OK: RPC Solana joignable et répond correctement "
            "(getHealth, getVersion, getSlot)."
        )
        sys.exit(0)
    else:
        logger.error(
            "TEST_SOLANA_RPC_FAILED: au moins un appel RPC a échoué. "
            "Voir les logs ci-dessus pour le détail."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
