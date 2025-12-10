# bot/core/rpc_clients.py

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, List
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

try:
    # Logger projet
    from bot.core.logging import get_logger

    logger = get_logger(__name__)
except Exception:  # pragma: no cover - fallback si logging custom non dispo
    logger = logging.getLogger(__name__)


# ======================================================================
# Helpers généraux
# ======================================================================


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: Any) -> Optional[str]:
    """
    Résout les variables d'environnement dans une chaîne de type:
        "${RPC_ETHEREUM_PRIMARY_HTTP}"
        "...${RPC_SOLANA_PRIMARY_HTTP}..."

    - Si la variable existe -> on remplace par sa valeur.
    - Si elle n'existe pas -> on retourne None (pour signaler "pas d'URL utilisable").

    Pour notre usage dans ce fichier, on considère qu'une URL doit être
    entièrement résolue: si après substitution il reste un "${...}", on ignore.
    """
    if not isinstance(value, str):
        return None

    # S'il n'y a pas de pattern ${...}, on retourne la valeur telle quelle
    if "${" not in value:
        return value

    def repl(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.getenv(var_name)
        if env_value is None:
            logger.warning(
                "rpc_clients: variable d'environnement %s non définie pour la valeur %r",
                var_name,
                value,
            )
            # On laisse le placeholder pour détecter plus tard que ce n'est pas OK
            return match.group(0)
        return env_value

    resolved = _ENV_PATTERN.sub(repl, value)

    # S'il reste encore des ${...}, on considère que ce n'est pas utilisable
    if "${" in resolved:
        logger.warning(
            "rpc_clients: la valeur %r contient encore des placeholders après substitution (résolu=%r)",
            value,
            resolved,
        )
        return None

    return resolved


# ======================================================================
# RPCClient bas niveau
# ======================================================================


@dataclass
class RPCClient:
    """
    Client RPC minimaliste pour une chain.

    - name       : nom de la chain (ethereum, arbitrum, base, bsc, solana, ...)
    - rpc_url    : URL RPC HTTP (QuickNode ou autre)
    - chain_id   : id EVM (1, 42161, 8453, 56, ...) si applicable
    - chain_type : "evm", "solana", etc. (permet d'adapter certains appels)
    """

    name: str
    rpc_url: str
    chain_id: Optional[int] = None
    chain_type: str = "evm"

    # ------------------------------------------------------------------
    # Helper générique JSON-RPC
    # ------------------------------------------------------------------
    def _rpc_call(
        self,
        method: str,
        params: Optional[List[Any]] = None,
        *,
        timeout: float = 5.0,
    ) -> Optional[Any]:
        """
        Appel JSON-RPC brut.

        Retourne payload["result"] ou None en cas d'erreur.
        """
        if params is None:
            params = []

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        data = json.dumps(payload).encode("utf-8")

        req = Request(
            self.rpc_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except (URLError, HTTPError) as e:
            logger.warning(
                "RPCClient %s: erreur lors de l'appel %s (%s)",
                self.name,
                method,
                e,
                extra={"event": "rpc_error", "chain": self.name, "rpc_url": self.rpc_url},
            )
            return None
        except Exception as e:  # pragma: no cover - défensif
            logger.warning(
                "RPCClient %s: exception lors de l'appel %s (%s)",
                self.name,
                method,
                e,
                extra={"event": "rpc_error", "chain": self.name, "rpc_url": self.rpc_url},
            )
            return None

        try:
            payload = json.loads(raw)
        except Exception:  # pragma: no cover - réponse non JSON
            logger.warning(
                "RPCClient %s: réponse non JSON pour %s",
                self.name,
                method,
                extra={"event": "rpc_bad_json", "chain": self.name, "rpc_url": self.rpc_url},
            )
            return None

        return payload.get("result")

    # ------------------------------------------------------------------
    # Helpers haut niveau (best-effort healthcheck)
    # ------------------------------------------------------------------
    def get_latest_block(self) -> Optional[int]:
        """
        Best-effort healthcheck.

        - pour EVM : appelle eth_blockNumber
        - pour Solana : appelle getSlot

        Retourne un int (numéro de block / slot) ou None en cas d'erreur.
        """
        if self.chain_type == "evm":
            result = self._rpc_call("eth_blockNumber", [])
            if isinstance(result, str) and result.startswith("0x"):
                try:
                    return int(result, 16)
                except ValueError:
                    return None
            return None

        if self.chain_type == "solana":
            # QuickNode / Solana standard supportent getSlot ou getBlockHeight.
            result = self._rpc_call("getSlot", [])
            if isinstance(result, int):
                return result
            try:
                return int(result)
            except (TypeError, ValueError):
                return None

        # Autres types : pas d'implémentation pour l'instant
        return None


# ======================================================================
# Helpers pour lire la config
# ======================================================================


def _extract_rpc_url_for_chain(
    *,
    chain_entry: Dict[str, Any],
    rpc_cfg: Dict[str, Any],
) -> Optional[str]:
    """
    Essaie de récupérer une URL RPC pour une chain donnée.

    Avec TON config.json, ça donne :

      - priorité à chain_entry["rpc_url"] (section "chains"),
      - sinon on regarde dans config["rpc"][chain_name]["primary_http"] ou "http"/"url"/"rpc_url".

    On résout les variables d'env de type ${RPC_*_PRIMARY_HTTP}.
    """
    # 1) direct dans chains[*].rpc_url
    raw_rpc_url = chain_entry.get("rpc_url")
    rpc_url = _resolve_env(raw_rpc_url) if raw_rpc_url is not None else None
    if rpc_url:
        return rpc_url

    name = str(chain_entry.get("name", "")).strip()
    if not name:
        return None

    chain_rpc = rpc_cfg.get(name) or {}
    if not isinstance(chain_rpc, dict):
        return None

    # 2) champs simples sur la chain dans "rpc"
    for key in ("http", "url", "rpc_url", "primary_http"):
        value = chain_rpc.get(key)
        resolved = _resolve_env(value)
        if resolved:
            return resolved

    # 3) nested "primary" (ex: {"primary": {"http": "..."}}
    primary = chain_rpc.get("primary") or {}
    if isinstance(primary, dict):
        for key in ("http", "url", "rpc_url"):
            value = primary.get(key)
            resolved = _resolve_env(value)
            if resolved:
                return resolved

    return None


# ======================================================================
# Builders principaux
# ======================================================================


def build_rpc_clients(cfg: Any) -> Dict[str, RPCClient]:
    """
    Construit un dict {chain_name: RPCClient} à partir de la config.

    On lit :
      - config["chains"] pour la liste des chains,
      - config["rpc"] comme source optionnelle d'URLs (primary_http, etc.).

    Pour chaque chain enabled=true :
      * on construit un RPCClient,
      * on tente un healthcheck via get_latest_block(),
      * on log :
          - "RPC OK: chain — block/slot N" si ça marche,
          - "RPC WARN: chain — impossible de récupérer le dernier block/slot" sinon.
    """
    # cfg peut être un dict (config brute) ou un objet BotConfig
    if isinstance(cfg, dict):
        chains_cfg = cfg.get("chains", []) or []
        rpc_cfg = cfg.get("rpc", {}) or {}
    else:
        chains_cfg = getattr(cfg, "chains", []) or []
        rpc_cfg = getattr(cfg, "rpc", {}) or {}

    clients: Dict[str, RPCClient] = {}

    for entry in chains_cfg:
        try:
            if not isinstance(entry, dict):
                continue

            name = str(entry.get("name", "")).strip()
            if not name:
                continue

            enabled = bool(entry.get("enabled", True))
            if not enabled:
                continue

            rpc_url = _extract_rpc_url_for_chain(
                chain_entry=entry,
                rpc_cfg=rpc_cfg,
            )
            if not rpc_url:
                logger.warning(
                    "build_rpc_clients: pas d'URL RPC utilisable pour chain=%s (entrée ignorée)",
                    name,
                    extra={"event": "rpc_missing_url", "chain": name},
                )
                continue

            chain_type = str(entry.get("type", "evm")).lower()
            chain_id = entry.get("chain_id")

            client = RPCClient(
                name=name,
                rpc_url=rpc_url,
                chain_id=chain_id,
                chain_type=chain_type,
            )

            latest_block = client.get_latest_block()
            if latest_block is not None:
                logger.info(
                    "RPC OK: %s — block/slot %s",
                    name,
                    latest_block,
                    extra={"event": "rpc_ok", "chain": name, "rpc_url": rpc_url},
                )
            else:
                logger.warning(
                    "RPC WARN: %s — impossible de récupérer le dernier block/slot",
                    name,
                    extra={"event": "rpc_warn", "chain": name, "rpc_url": rpc_url},
                )

            clients[name] = client

        except Exception as e:  # pragma: no cover - défensif
            logger.warning(
                "Erreur lors de l'initialisation RPC pour une chain: %s",
                e,
                extra={"event": "rpc_init_error"},
            )

    return clients


def build_rpc_clients_from_config(
    raw_cfg: Dict[str, Any],
    *,
    run_mode: str = "PAPER",
) -> Dict[str, RPCClient]:
    """
    Builder utilisé par ExecutionWithRisk.

    - raw_cfg : config globale (dict déjà chargé depuis config.json)
    - run_mode : "PAPER" ou "LIVE" (pour l'instant purement informatif)

    Pour M10/M11 :
      - on construit les RPC clients **en lecture seule**,
      - ça sert à la santé / préparation M11 DRY-RUN,
      - aucune TX réelle n'est envoyée (ton ExecutionEngine est en STUB).
    """
    mode = str(run_mode or "PAPER").upper()

    clients = build_rpc_clients(raw_cfg)

    if not clients:
        logger.info(
            "build_rpc_clients_from_config: aucun client RPC construit (run_mode=%s).",
            mode,
        )
    else:
        logger.info(
            "build_rpc_clients_from_config: %d client(s) RPC construit(s) (run_mode=%s, chains=%s).",
            len(clients),
            mode,
            list(clients.keys()),
        )

    return clients

