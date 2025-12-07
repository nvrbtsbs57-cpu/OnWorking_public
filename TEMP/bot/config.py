from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ====================================================================
# LOGGING
# ====================================================================

@dataclass
class LoggingConfig:
    level: str = "INFO"
    json: bool = True


# ====================================================================
# INDEXER CONFIG
# ====================================================================

@dataclass
class ChainRpcConfig:
    name: str
    rpc_url: str
    enabled: bool = True
    start_block: int = 0
    max_blocks_per_poll: int = 50


@dataclass
class IndexerConfig:
    enabled: bool = True
    poll_interval_seconds: float = 2.0
    storage_path: str = "data/indexer"
    chains: dict[str, ChainRpcConfig] = field(default_factory=dict)


# ====================================================================
# NORMALIZER
# ====================================================================

@dataclass
class NormalizerConfig:
    enabled: bool = True
    history_size: int = 200
    min_candles_for_pattern: int = 10


# ====================================================================
# AGENT
# ====================================================================

@dataclass
class AgentConfig:
    enabled: bool = True
    max_open_positions: int = 0
    risk_per_trade: float = 0.0


# ====================================================================
# API
# ====================================================================

@dataclass
class ApiConfig:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8000


# ====================================================================
# ALERTING
# ====================================================================

@dataclass
class AlertingConfig:
    enabled: bool = True
    channel: str = "console"


# ====================================================================
# CONFIG PRINCIPALE
# ====================================================================

@dataclass
class BotConfig:
    mode: str
    logging: LoggingConfig
    indexer: IndexerConfig
    normalizer: NormalizerConfig
    agent: AgentConfig
    api: ApiConfig
    alerting: AlertingConfig


# ====================================================================
# PARSEURS
# ====================================================================

def _as_logging(data: dict[str, Any]) -> LoggingConfig:
    return LoggingConfig(
        level=data.get("level", "INFO"),
        json=bool(data.get("json", True)),
    )


def _as_indexer(data: dict[str, Any]) -> IndexerConfig:
    chains_cfg_raw = data.get("chains", {})
    chains: dict[str, ChainRpcConfig] = {}

    if isinstance(chains_cfg_raw, dict):
        for name, c in chains_cfg_raw.items():
            if not isinstance(c, dict):
                continue
            rpc_url = c.get("rpc_url")
            if not rpc_url:
                continue

            chains[name] = ChainRpcConfig(
                name=name,
                rpc_url=str(rpc_url),
                enabled=bool(c.get("enabled", True)),
                start_block=int(c.get("start_block", 0)),
                max_blocks_per_poll=int(c.get("max_blocks_per_poll", 50)),
            )

    return IndexerConfig(
        enabled=bool(data.get("enabled", True)),
        poll_interval_seconds=float(data.get("poll_interval_seconds", 2.0)),
        storage_path=data.get("storage_path", "data/indexer"),
        chains=chains,
    )


def _as_normalizer(data: dict[str, Any]) -> NormalizerConfig:
    return NormalizerConfig(
        enabled=bool(data.get("enabled", True)),
        history_size=int(data.get("history_size", 200)),
        min_candles_for_pattern=int(data.get("min_candles_for_pattern", 10)),
    )


def _as_agent(data: dict[str, Any]) -> AgentConfig:
    return AgentConfig(
        enabled=bool(data.get("enabled", True)),
        max_open_positions=int(data.get("max_open_positions", 0)),
        risk_per_trade=float(data.get("risk_per_trade", 0.0)),
    )


def _as_api(data: dict[str, Any]) -> ApiConfig:
    return ApiConfig(
        enabled=bool(data.get("enabled", True)),
        host=data.get("host", "127.0.0.1"),
        port=int(data.get("port", 8000)),
    )


def _as_alerting(data: dict[str, Any]) -> AlertingConfig:
    return AlertingConfig(
        enabled=bool(data.get("enabled", True)),
        channel=data.get("channel", "console"),
    )


# ====================================================================
# LOADER GLOBAL
# ====================================================================

def load_config(path: str | Path) -> BotConfig:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))

    return BotConfig(
        mode=raw.get("mode", "GODMODE"),
        logging=_as_logging(raw.get("logging", {})),
        indexer=_as_indexer(raw.get("indexer", {})),
        normalizer=_as_normalizer(raw.get("normalizer", {})),
        agent=_as_agent(raw.get("agent", {})),
        api=_as_api(raw.get("api", {})),
        alerting=_as_alerting(raw.get("alerting", {})),
    )
