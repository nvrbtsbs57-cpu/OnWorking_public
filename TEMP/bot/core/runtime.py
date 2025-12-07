# bot/core/runtime.py

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Protocol, Sequence, Tuple, List

from bot.core.risk import RiskConfig, RiskEngine as RealRiskEngine
from bot.core.signals import TradeSignal, SignalSide, SignalKind


# ======================================================================
# Modes globaux
# ======================================================================


class ExecutionMode(str, Enum):
    """
    Mode d'exécution global du bot.

    PAPER_ONCHAIN : vraies données on-chain, aucune tx réelle.
    LIVE          : exécution réelle on-chain (PAS encore autorisée).
    """

    PAPER_ONCHAIN = "PAPER_ONCHAIN"
    LIVE = "LIVE"


class SafetyMode(str, Enum):
    """
    Profil de sécurité global.
    SAFE   : très conservateur.
    NORMAL : défaut.
    DEGEN  : permissif (sandbox, petits montants).
    """

    SAFE = "SAFE"
    NORMAL = "NORMAL"
    DEGEN = "DEGEN"


# ======================================================================
# Protocols (interfaces runtime)
# ======================================================================


class RiskEngineIface(Protocol):
    def apply_global_limits(
        self, signals: Sequence[Any], safety_mode: SafetyMode
    ) -> Sequence[Any]:
        ...

    def on_tick(self) -> None:
        ...


class WalletManagerIface(Protocol):
    def refresh_balances(self) -> None:
        ...

    def on_tick(self) -> None:
        ...


class ExecutionEngineIface(Protocol):
    def process_signals(
        self, signals: Sequence[Any], mode: ExecutionMode
    ) -> None:
        ...

    def on_tick(self) -> None:
        ...


class FinanceEngineIface(Protocol):
    def on_tick(self) -> None:
        ...


class StrategyEngineIface(Protocol):
    def next_signals(self) -> Sequence[Any]:
        ...

    def on_tick(self) -> None:
        ...


class MonitoringIface(Protocol):
    def on_startup(self, config: "RuntimeConfig") -> None:
        ...

    def on_tick(self) -> None:
        ...

    def on_shutdown(self) -> None:
        ...


# ======================================================================
# Config & deps
# ======================================================================


@dataclass
class RuntimeConfig:
    """
    Config légère pour le runtime.
    Le détail (risk par wallet, paramètres stratégie, etc.) vit dans les modules M2–M7.
    """

    # Modes globaux
    execution_mode: ExecutionMode = ExecutionMode.PAPER_ONCHAIN
    safety_mode: SafetyMode = SafetyMode.SAFE

    # Timing
    tick_interval_seconds: float = 1.0

    # Feature flags
    enable_finance: bool = True
    enable_monitoring: bool = True

    # Metadata
    bot_name: str = "BOT_GODMODE"
    chain_id: Optional[int] = None

    # Extra (pour passer des trucs spécifiques sans casser la signature)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeDeps:
    """
    Dépendances concrètes injectées dans le runtime.
    Construites depuis la config globale (M2–M7).
    """

    risk_engine: RiskEngineIface
    wallet_manager: WalletManagerIface
    execution_engine: ExecutionEngineIface
    strategy_engine: StrategyEngineIface
    finance_engine: Optional[FinanceEngineIface] = None
    monitoring: Optional[MonitoringIface] = None


# ======================================================================
# BotRuntime (M1)
# ======================================================================


class BotRuntime:
    """
    Orchestrateur central (M1) :

    - Demande des signaux aux stratégies
    - Applique le RiskEngine global + SafetyMode
    - Route les signaux vers l'ExecutionEngine (PAPER_ONCHAIN / LIVE)
    - Laisse Finance / Monitoring tourner à chaque tick

    Important : ce runtime ne connaît PAS les détails internes des modules,
    il ne manipule que les interfaces définies plus haut.
    """

    def __init__(self, config: RuntimeConfig, deps: RuntimeDeps) -> None:
        self.config = config
        self.deps = deps
        self._logger = logging.getLogger(__name__)
        self._stopping = False

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._logger.info(
            "BotRuntime.start() — bot_name=%s, execution_mode=%s, safety_mode=%s",
            self.config.bot_name,
            self.config.execution_mode.value,
            self.config.safety_mode.value,
        )
        self._validate_config()
        self._install_signal_handlers()

        if self.deps.monitoring and self.config.enable_monitoring:
            self.deps.monitoring.on_startup(self.config)

    def run_forever(self) -> None:
        """Boucle principale synchrone."""
        self.start()
        try:
            while not self._stopping:
                self._tick_once()
                time.sleep(self.config.tick_interval_seconds)
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._logger.info("BotRuntime.stop() demandé.")
        self._stopping = True

    # ------------------------------------------------------------------
    # Internes
    # ------------------------------------------------------------------

    def _validate_config(self) -> None:
        # LIVE interdit tant que M10 n'est pas fait
        if self.config.execution_mode is ExecutionMode.LIVE:
            raise RuntimeError(
                "ExecutionMode.LIVE n'est pas autorisé tant que le passage "
                "LIVE n'a pas été validé (M10). Utiliser PAPER_ONCHAIN."
            )

    def _install_signal_handlers(self) -> None:
        def handler(sig: int, frame: Any) -> None:  # type: ignore[override]
            self._logger.info("Signal %s reçu, arrêt du runtime...", sig)
            self.stop()

        try:
            signal.signal(signal.SIGINT, handler)
            signal.signal(signal.SIGTERM, handler)
        except ValueError:
            # Cas: pas dans le main thread / env qui ne supporte pas les signaux
            self._logger.debug(
                "Impossible d'installer les signal handlers (non-main thread ou env limité)."
            )

    def _tick_once(self) -> None:
        """
        Un tick = un cycle complet de décision/exécution.
        L'ordre est important.
        """

        # 1) Wallets: refresh des balances + logique périodique
        self.deps.wallet_manager.refresh_balances()
        self.deps.wallet_manager.on_tick()

        # 2) Stratégies: génération de signaux bruts
        raw_signals = self.deps.strategy_engine.next_signals()
        self.deps.strategy_engine.on_tick()

        # 3) Risk global: clamp / filtrage
        filtered_signals = self.deps.risk_engine.apply_global_limits(
            raw_signals, self.config.safety_mode
        )
        self.deps.risk_engine.on_tick()

        # 4) Execution (PAPER_ONCHAIN / LIVE)
        self.deps.execution_engine.process_signals(
            filtered_signals,
            mode=self.config.execution_mode,
        )
        self.deps.execution_engine.on_tick()

        # 5) Finance (PNL, fees, profit box, etc.)
        if self.config.enable_finance and self.deps.finance_engine is not None:
            self.deps.finance_engine.on_tick()

        # 6) Monitoring (logs, métriques, alerts)
        if self.config.enable_monitoring and self.deps.monitoring is not None:
            self.deps.monitoring.on_tick()

    def _shutdown(self) -> None:
        self._logger.info("BotRuntime._shutdown() — arrêt propre.")
        if self.deps.monitoring and self.config.enable_monitoring:
            try:
                self.deps.monitoring.on_shutdown()
            except Exception:
                self._logger.exception(
                    "Erreur lors de monitoring.on_shutdown()."
                )


# ======================================================================
# Stubs & multi-stratégies
# ======================================================================


class StubStrategyEngine(StrategyEngineIface):
    def __init__(self) -> None:
        self._logger = logging.getLogger("StubStrategyEngine")
        self._tick_count = 0

    def next_signals(self) -> Sequence[TradeSignal]:
        """
        Émet périodiquement un TradeSignal stub pour tester le pipeline.

        Tous les 15 ticks :
          - wallet_id: "sniper_sol" (exemple)
          - symbol: "FAKE/USDC"
          - side: BUY
          - notional_usd: 10.0
        """
        self._tick_count += 1
        if self._tick_count % 15 == 0:
            sig = TradeSignal(
                id=f"stub-{self._tick_count}",
                strategy_id="stub_strategy",
                wallet_id="sniper_sol",  # wallet d'exemple
                symbol="FAKE/USDC",
                side=SignalSide.BUY,
                notional_usd=10.0,
                kind=SignalKind.ENTRY,
                meta={"source": "stub_strategy"},
            )
            self._logger.info(
                "next_signals() — émission d'un TradeSignal stub: %s %s %.2f USD",
                sig.symbol,
                sig.side.value,
                sig.notional_usd,
            )
            return [sig]
        return []

    def on_tick(self) -> None:
        pass


class MultiStrategyEngine(StrategyEngineIface):
    """
    Agrégateur de plusieurs StrategyEngineIface.

    - Appelle next_signals() sur chaque stratégie,
    - concatène tous les signaux dans une seule liste,
    - propage on_tick() à toutes les stratégies.

    Si une stratégie plante, on loggue l'exception et on continue avec les autres.
    """

    def __init__(self, engines: Sequence[StrategyEngineIface]) -> None:
        self._engines = list(engines)
        self._logger = logging.getLogger("MultiStrategyEngine")

    def next_signals(self) -> Sequence[TradeSignal]:
        all_signals: List[TradeSignal] = []
        for eng in self._engines:
            try:
                sigs = eng.next_signals()
            except Exception:
                self._logger.exception(
                    "Erreur dans next_signals() d'une stratégie, ignorée."
                )
                continue
            if sigs:
                all_signals.extend(sigs)

        if all_signals:
            self._logger.debug(
                "MultiStrategyEngine — %d signaux agrégés de %d stratégies.",
                len(all_signals),
                len(self._engines),
            )
        return all_signals

    def on_tick(self) -> None:
        for eng in self._engines:
            try:
                eng.on_tick()
            except Exception:
                self._logger.exception(
                    "Erreur dans on_tick() d'une stratégie, ignorée."
                )


class StubFinanceEngine(FinanceEngineIface):
    def __init__(self) -> None:
        self._logger = logging.getLogger("StubFinanceEngine")
        self._tick_count = 0

    def on_tick(self) -> None:
        self._tick_count += 1
        if self._tick_count % 30 == 0:
            self._logger.info(
                "FinanceEngine stub — agrégation PnL périodique."
            )


class StubMonitoring(MonitoringIface):
    def __init__(self) -> None:
        self._logger = logging.getLogger("StubMonitoring")

    def on_startup(self, config: RuntimeConfig) -> None:
        self._logger.info(
            "Monitoring startup — bot_name=%s, mode=%s, safety=%s",
            config.bot_name,
            config.execution_mode.value,
            config.safety_mode.value,
        )

    def on_tick(self) -> None:
        pass

    def on_shutdown(self) -> None:
        self._logger.info("Monitoring shutdown — stub.")


# ======================================================================
# Helpers FinanceEngine
# ======================================================================


def _extract_wallet_flows_engine(wallet_manager: WalletManagerIface):
    """Essaie de récupérer le WalletFlowsEngine interne au RuntimeWalletManager.

    On reste défensif :
      - on teste plusieurs noms d'attributs possibles,
      - on tente également quelques getters classiques,
      - en cas d'échec, on loggue et on renvoie None pour activer le StubFinanceEngine.
    """
    try:
        from bot.wallets.engine import WalletFlowsEngine  # type: ignore
    except Exception:
        logging.getLogger(__name__).exception(
            "Impossible d'importer WalletFlowsEngine."
        )
        return None

    # 1) Attributs directs possibles
    candidate_attrs = ["flows_engine", "wallet_engine", "engine", "_engine"]
    for attr in candidate_attrs:
        if hasattr(wallet_manager, attr):
            val = getattr(wallet_manager, attr, None)
            if isinstance(val, WalletFlowsEngine):
                return val

    # 2) Méthodes getters possibles
    candidate_methods = ["get_flows_engine", "get_wallet_engine", "get_engine"]
    for name in candidate_methods:
        fn = getattr(wallet_manager, name, None)
        if callable(fn):
            try:
                val = fn()
            except Exception:
                continue
            else:
                if isinstance(val, WalletFlowsEngine):
                    return val

    logging.getLogger(__name__).warning(
        "Impossible d'extraire WalletFlowsEngine depuis WalletManager; "
        "FinanceEngine concret désactivé (fallback StubFinanceEngine)."
    )
    return None


def _build_finance_engine_from_config(
    raw_cfg: Dict[str, Any],
    wallet_manager: WalletManagerIface,
) -> FinanceEngineIface:
    """Instancie un FinanceEngine concret + FinancePipeline si possible, sinon StubFinanceEngine.

    - importe bot.finance.engine.FinanceEngine / FinanceEngineConfig,
      et bot.finance.pipeline.FinanceConfig / FinancePipeline,
    - extrait le WalletFlowsEngine du RuntimeWalletManager,
    - crée un FinanceConfig + FinancePipeline à partir de config.json,
    - instancie FinanceEngine avec ce pipeline.
    """
    log = logging.getLogger(__name__)
    try:
        from bot.finance.engine import FinanceEngine, FinanceEngineConfig  # type: ignore
        from bot.finance.pipeline import (  # type: ignore
            FinanceConfig,
            FinancePipeline,
        )
    except Exception:
        log.exception(
            "FinanceEngine ou FinancePipeline non disponibles (import), "
            "fallback sur StubFinanceEngine."
        )
        return StubFinanceEngine()

    flows_engine = _extract_wallet_flows_engine(wallet_manager)
    if flows_engine is None:
        return StubFinanceEngine()

    try:
        finance_cfg = FinanceConfig.from_global_config(raw_cfg)
    except Exception:
        log.exception(
            "Erreur lors de la construction de FinanceConfig, "
            "fallback sur StubFinanceEngine."
        )
        return StubFinanceEngine()

    wallet_roles = raw_cfg.get("wallet_roles", {}) or {}
    wallets_cfg = raw_cfg.get("wallets", []) or []

    try:
        pipeline = FinancePipeline(
            config=finance_cfg,
            wallet_roles=wallet_roles,
            wallets_cfg=wallets_cfg,
        )

        engine_cfg = FinanceEngineConfig(
            enable_auto_fees=finance_cfg.autofees.enabled,
            # profit_split reste interne au WalletFlowsEngine, on le laisse activé
            enable_profit_split=True,
            enable_compounding=finance_cfg.compounding.enabled,
        )

        engine = FinanceEngine(
            wallet_engine=flows_engine,
            cfg=engine_cfg,
            pipeline=pipeline,
            logger=logging.getLogger("FinanceEngine"),
        )
        return engine
    except Exception:
        log.exception(
            "Erreur lors de l'initialisation du FinanceEngine avec FinancePipeline, "
            "fallback sur StubFinanceEngine."
        )
        return StubFinanceEngine()


# ======================================================================
# Builders de runtime
# ======================================================================


def build_runtime_from_config(
    raw_cfg: Dict[str, Any]
) -> Tuple[RuntimeConfig, RuntimeDeps]:
    """
    Builder de runtime qui lit la config.json :
    - RUN_MODE / SAFETY_MODE -> ExecutionMode / SafetyMode
    - section risk -> vrai RiskEngine
    - WalletManager runtime basé sur WalletFlowsEngine (M3)
    - FinanceEngine concret + FinancePipeline (si dispo) ou stub
    - stratégie & monitoring restent en stub pour l'instant.
    """

    # Modes globaux
    run_mode_str = str(raw_cfg.get("RUN_MODE", "paper")).lower()
    safety_mode_raw = str(raw_cfg.get("SAFETY_MODE", "safe")).lower()

    if safety_mode_raw in ("safe", "normal", "degen"):
        safety_mode = SafetyMode[safety_mode_raw.upper()]
    else:
        safety_mode = SafetyMode.SAFE

    if run_mode_str in ("paper", "backtest"):
        execution_mode = ExecutionMode.PAPER_ONCHAIN
    elif run_mode_str == "live":
        execution_mode = ExecutionMode.LIVE
    else:
        execution_mode = ExecutionMode.PAPER_ONCHAIN

    tick_interval = float(raw_cfg.get("RUNTIME_TICK_INTERVAL_SECONDS", 1.0))
    bot_name = str(raw_cfg.get("BOT_NAME", "BOT_GODMODE"))
    chain_id = raw_cfg.get("CHAIN_ID")

    config = RuntimeConfig(
        execution_mode=execution_mode,
        safety_mode=safety_mode,
        tick_interval_seconds=tick_interval,
        bot_name=bot_name,
        chain_id=chain_id,
    )

    # Vrai RiskEngine à partir de la section "risk"
    risk_cfg_raw = raw_cfg.get("risk", {}) or {}
    risk_config = RiskConfig.from_dict(risk_cfg_raw).adjusted_for_safety(
        safety_mode.value
    )
    risk_engine = RealRiskEngine(risk_config)

    # WalletManager runtime basé sur WalletFlowsEngine (M3)
    from bot.wallets.runtime_manager import (  # import local pour éviter les cycles
        RuntimeWalletManager,
    )

    wallet_manager = RuntimeWalletManager.from_config(raw_cfg)

    # Brancher les métriques wallets dans le RiskEngine (M2 <-> M3)
    if hasattr(risk_engine, "set_wallet_metrics"):
        risk_engine.set_wallet_metrics(wallet_manager)

    # ExecutionEngine réel (papier) aware du RiskEngine + WalletManager
    from bot.core.execution import (  # import local pour éviter les cycles
        RiskAwareExecutionEngine,
    )

    execution_engine = RiskAwareExecutionEngine(
        risk_engine=risk_engine,
        wallet_manager=wallet_manager,
    )

    # FinanceEngine concret (M4-core/full papier) ou stub si indisponible
    finance_engine = _build_finance_engine_from_config(raw_cfg, wallet_manager)

    deps = RuntimeDeps(
        risk_engine=risk_engine,
        wallet_manager=wallet_manager,
        execution_engine=execution_engine,
        strategy_engine=StubStrategyEngine(),
        finance_engine=finance_engine,
        monitoring=StubMonitoring(),
    )

    return config, deps


def build_runtime_memecoin_from_config(
    raw_cfg: Dict[str, Any]
) -> Tuple[RuntimeConfig, RuntimeDeps]:
    """
    Variante de build_runtime_from_config qui branche :
      - stratégie memecoin (M5-lite++) via MemecoinStrategyEngine
      - stratégie copy trading (M6-lite) via CopyTradingStrategyEngine
      - le tout agrégé dans un MultiStrategyEngine.

    Stack :
      - RiskEngine réel (M2)
      - RuntimeWalletManager + WalletFlowsEngine (M3)
      - RiskAwareExecutionEngine (M7-lite++)
      - MemecoinStrategyEngine + CopyTradingStrategyEngine (M5/M6)
      - FinanceEngine concret (M4-core/full papier) + Monitoring stub
    """

    # Modes globaux (identique à build_runtime_from_config)
    run_mode_str = str(raw_cfg.get("RUN_MODE", "paper")).lower()
    safety_mode_raw = str(raw_cfg.get("SAFETY_MODE", "safe")).lower()

    if safety_mode_raw in ("safe", "normal", "degen"):
        safety_mode = SafetyMode[safety_mode_raw.upper()]
    else:
        safety_mode = SafetyMode.SAFE

    if run_mode_str in ("paper", "backtest"):
        execution_mode = ExecutionMode.PAPER_ONCHAIN
    elif run_mode_str == "live":
        execution_mode = ExecutionMode.LIVE
    else:
        execution_mode = ExecutionMode.PAPER_ONCHAIN

    tick_interval = float(raw_cfg.get("RUNTIME_TICK_INTERVAL_SECONDS", 1.0))
    bot_name = str(raw_cfg.get("BOT_NAME", "BOT_GODMODE_MEME"))
    chain_id = raw_cfg.get("CHAIN_ID")

    config = RuntimeConfig(
        execution_mode=execution_mode,
        safety_mode=safety_mode,
        tick_interval_seconds=tick_interval,
        bot_name=bot_name,
        chain_id=chain_id,
    )

    # --- RiskEngine réel ------------------------------------------------
    risk_cfg_raw = raw_cfg.get("risk", {}) or {}
    risk_config = RiskConfig.from_dict(risk_cfg_raw).adjusted_for_safety(
        safety_mode.value
    )
    risk_engine = RealRiskEngine(risk_config)

    # --- WalletManager runtime basé sur WalletFlowsEngine ----------------
    from bot.wallets.runtime_manager import RuntimeWalletManager  # type: ignore

    wallet_manager = RuntimeWalletManager.from_config(raw_cfg)

    if hasattr(risk_engine, "set_wallet_metrics"):
        risk_engine.set_wallet_metrics(wallet_manager)

    # --- ExecutionEngine réel (papier) ----------------------------------
    from bot.core.execution import RiskAwareExecutionEngine  # type: ignore

    execution_engine = RiskAwareExecutionEngine(
        risk_engine=risk_engine,
        wallet_manager=wallet_manager,
    )

    # --- StrategyEngines (memecoin + copy_trading) ----------------------
    from bot.strategies.memecoin_farming.agent import (  # type: ignore
        build_memecoin_strategy_from_config,
    )
    from bot.strategies.copy_trading.agent import (  # type: ignore
        build_copy_trading_strategy_from_config,
    )

    meme_strategy = build_memecoin_strategy_from_config(
        raw_cfg,
        logger_=logging.getLogger("MemecoinStrategy"),
    )
    copy_strategy = build_copy_trading_strategy_from_config(
        raw_cfg,
        logger_=logging.getLogger("CopyTradingStrategy"),
    )

    strategy_engine: StrategyEngineIface = MultiStrategyEngine(
        [meme_strategy, copy_strategy]
    )

    # --- FinanceEngine concret + Monitoring stub ------------------------
    finance_engine = _build_finance_engine_from_config(raw_cfg, wallet_manager)
    monitoring = StubMonitoring()

    deps = RuntimeDeps(
        risk_engine=risk_engine,
        wallet_manager=wallet_manager,
        execution_engine=execution_engine,
        strategy_engine=strategy_engine,
        finance_engine=finance_engine,
        monitoring=monitoring,
    )

    return config, deps
