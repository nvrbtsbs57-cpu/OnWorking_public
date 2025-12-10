# bot/trading/execution_risk_factory.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from bot.core.risk import RiskConfig, RiskEngine
from bot.trading.execution import ExecutionEngine
from bot.trading.execution_risk_adapter import (
    ExecutionRiskAdapter,
    RuntimeWalletStats,
    WalletStatsProvider,
)

log = logging.getLogger(__name__)


@dataclass
class ExecutionRiskBundle:
    """
    Petit container pratique pour exposer tout ce qui touche à l'exécution + risque.

    - engine       : l'ExecutionEngine final à utiliser (souvent ExecutionRiskAdapter)
    - risk_engine  : RiskEngine sous-jacent (pour debug, métriques, etc.)
    - wallet_stats : provider RuntimeWalletStats branché sur RuntimeWalletManager
    """

    engine: ExecutionEngine
    risk_engine: RiskEngine
    wallet_stats: WalletStatsProvider


def _build_risk_config_from_mapping(
    mapping: Mapping[str, Any],
    *,
    root_config: Optional[Mapping[str, Any]] = None,
) -> RiskConfig:
    """
    Helper tolérant pour construire un RiskConfig à partir d'un dict.

    Il essaie plusieurs conventions possibles pour éviter de casser l'existant :
      - RiskConfig.from_dict(mapping)
      - RiskConfig.from_config(mapping)
      - RiskConfig.from_config(root_config)  (si fourni)
      - RiskConfig(**mapping)
    """
    # mapping = bloc "risk" ; root_config = config.json complet éventuel

    # Cas "pas de bloc risk" : on tente un RiskConfig par défaut
    if not mapping and root_config is None:
        try:
            return RiskConfig()  # type: ignore[call-arg]
        except TypeError:
            log.error("Impossible de construire RiskConfig sans paramètres")
            raise

    # 1) from_dict(mapping)
    factory = getattr(RiskConfig, "from_dict", None)
    if callable(factory):
        try:
            return factory(mapping)  # type: ignore[misc]
        except TypeError:
            # Mauvaise signature, on continue
            pass

    # 2) from_config(mapping)
    factory = getattr(RiskConfig, "from_config", None)
    if callable(factory):
        # 2.a) avec le bloc risk
        try:
            return factory(mapping)  # type: ignore[misc]
        except TypeError:
            # 2.b) avec la config globale si fournie
            if root_config is not None:
                try:
                    return factory(root_config)  # type: ignore[misc]
                except TypeError:
                    pass

    # 3) Fallback générique : on suppose que RiskConfig est un dataclass / pydantic-like
    try:
        return RiskConfig(**mapping)  # type: ignore[call-arg]
    except TypeError:
        log.error("Impossible de construire RiskConfig à partir du mapping risk=%r", mapping)
        raise


def build_execution_with_risk_from_config(
    *,
    root_config: Mapping[str, Any],
    base_engine: ExecutionEngine,
    wallet_manager: Any,
    logger: Optional[logging.Logger] = None,
) -> ExecutionRiskBundle:
    """
    Construit la couche 'ExecutionRisk' par dessus un ExecutionEngine existant.

    root_config    : config globale (celle de config.json déjà chargée)
    base_engine    : moteur d'exécution nu (PaperExecutionEngine ou live ExecutionEngine)
    wallet_manager : RuntimeWalletManager / WalletFlowsEngine déjà initialisé
    logger         : logger optionnel (sinon __name__)

    Retourne un ExecutionRiskBundle avec :
      - engine      : soit base_engine (si risque désactivé), soit ExecutionRiskAdapter
      - risk_engine : RiskEngine configuré
      - wallet_stats: RuntimeWalletStats branché sur wallet_manager
    """
    logger = logger or log

    risk_block: Mapping[str, Any] = root_config.get("risk", {}) or {}
    risk_config = _build_risk_config_from_mapping(
        risk_block,
        root_config=root_config,
    )

    # Construction du RiskEngine, en essayant d'injecter un logger si supporté
    try:
        risk_engine = RiskEngine(risk_config, logger=logger)  # type: ignore[call-arg]
    except TypeError:
        risk_engine = RiskEngine(risk_config)  # type: ignore[call-arg]

    # Provider de stats runtime basé sur WalletFlowsEngine / RuntimeWalletManager
    wallet_stats = RuntimeWalletStats(wallet_manager=wallet_manager, logger=logger)

    # Flag d'activation : on regarde d'abord la config JSON, puis un éventuel
    # attribut risk_config.enabled
    cfg_enabled = bool(risk_block.get("enabled", True))
    enabled = getattr(risk_config, "enabled", cfg_enabled)

    if not enabled:
        logger.warning(
            "Execution risk désactivé dans la config (risk.enabled = false). "
            "On retourne base_engine sans wrapper ExecutionRiskAdapter."
        )
        return ExecutionRiskBundle(
            engine=base_engine,
            risk_engine=risk_engine,
            wallet_stats=wallet_stats,
        )

    wrapped_engine = ExecutionRiskAdapter(
        inner_engine=base_engine,
        risk_engine=risk_engine,
        wallet_stats=wallet_stats,
        logger=logger,
    )

    logger.info("ExecutionRiskAdapter initialisé et branché sur ExecutionEngine")

    return ExecutionRiskBundle(
        engine=wrapped_engine,
        risk_engine=risk_engine,
        wallet_stats=wallet_stats,
    )


def build_execution_engine_with_risk(
    *,
    root_config: Mapping[str, Any],
    base_engine: ExecutionEngine,
    wallet_manager: Any,
    logger: Optional[logging.Logger] = None,
) -> ExecutionEngine:
    """
    Version courte quand tu n'as besoin que de l'engine final.

    Exemple :
        base_engine = PaperExecutionEngine(paper_trader=paper_trader)
        execution_engine = build_execution_engine_with_risk(
            root_config=config,
            base_engine=base_engine,
            wallet_manager=wallet_manager,
        )
    """
    bundle = build_execution_with_risk_from_config(
        root_config=root_config,
        base_engine=base_engine,
        wallet_manager=wallet_manager,
        logger=logger,
    )
    return bundle.engine


__all__ = [
    "ExecutionRiskBundle",
    "build_execution_with_risk_from_config",
    "build_execution_engine_with_risk",
]
