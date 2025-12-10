from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple, Protocol


from bot.core.logging import get_logger, setup_logging
from bot.wallets.runtime_manager import RuntimeWalletManager
from bot.trading.execution import ExecutionEngine
from bot.trading.paper_trader import PaperTrader, PaperTraderConfig
from bot.strategies.memecoin_farming.agent import (
    MemecoinStrategyEngine,
    build_memecoin_strategy_from_config,
)

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

# Ce fichier est dans:
#   bot/strategies/memecoin_farming/runtime.py
# donc parents[3] = racine projet BOT_GODMODE/BOT_GODMODE
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_PATH = _PROJECT_ROOT / "config.json"

_log = get_logger("memecoin_runtime")


def load_config() -> Dict[str, Any]:
    """Charge config.json à la racine du projet."""
    if not _CONFIG_PATH.exists():
        raise SystemExit(f"[FATAL] config.json introuvable à: {_CONFIG_PATH}")
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def setup_logging_from_config(raw_cfg: Dict[str, Any]) -> None:
    """Initialise le logging global à partir de config["logging"].

    Exemple dans config.json :

      "logging": {
        "level": "INFO",
        "json": true
      }
    """
    level = "INFO"
    json_mode = True
    try:
        if isinstance(raw_cfg, dict):
            log_cfg = raw_cfg.get("logging") or {}
        else:
            log_cfg = {}
        level = str(log_cfg.get("level", level))
        json_mode = bool(log_cfg.get("json", json_mode))
    except Exception:
        # tolérant, on garde les valeurs par défaut
        pass

    setup_logging(level=level, json_mode=json_mode)


# ---------------------------------------------------------------------------
# Builders (wallets, exec, stratégie)
# ---------------------------------------------------------------------------

def build_runtime_wallet_manager(
    cfg: Dict[str, Any],
    logger_: Optional[logging.Logger] = None,
) -> RuntimeWalletManager:
    """Construit le RuntimeWalletManager à partir de config.json.

    S'appuie sur RuntimeWalletManager.from_config(cfg, logger=...).
    """
    log = logger_ or _log
    try:
        mgr = RuntimeWalletManager.from_config(cfg, logger=log)
    except TypeError:
        log.error(
            "RuntimeWalletManager.from_config(...) a une signature différente.\n"
            "Adapte build_runtime_wallet_manager() en conséquence."
        )
        raise

    log.info("RuntimeWalletManager initialisé à partir de config.json.")
    return mgr


def build_execution_engine(
    runtime_wallet_manager: RuntimeWalletManager,
    logger_: Optional[logging.Logger] = None,
) -> ExecutionEngine:
    """Construit l'ExecutionEngine PAPER (PaperTrader + RuntimeWalletManager)."""
    log = logger_ or _log

    pt_cfg = PaperTraderConfig.from_env()
    paper_trader = PaperTrader(config=pt_cfg)

    exec_engine = ExecutionEngine(
        inner_engine=paper_trader,
        wallet_manager=runtime_wallet_manager,
    )

    log.info(
        "ExecutionEngine PAPER initialisé avec PaperTrader + RuntimeWalletManager "
        "(trades -> data/godmode/trades.jsonl)."
    )
    return exec_engine


def build_memecoin_engine(
    cfg: Dict[str, Any],
    logger_: Optional[logging.Logger] = None,
) -> MemecoinStrategyEngine:
    """Construit le moteur de stratégie memecoin via la factory officielle.

    Délègue à build_memecoin_strategy_from_config() défini dans agent.py.
    """
    log = logger_ or _log
    engine = build_memecoin_strategy_from_config(cfg, logger_=log)
    log.info(
        "MemecoinStrategyEngine initialisé via "
        "build_memecoin_strategy_from_config()."
    )
    return engine


# ---------------------------------------------------------------------------
# Config runtime (override CLI)
# ---------------------------------------------------------------------------

@dataclass
class MemecoinRuntimeConfig:
    """Config runtime "live-like" pour la strat memecoin.

    Ces valeurs peuvent être override par :
    - config.json["strategies"]["memecoin_farming"]["pairs"][0]
    - les arguments CLI (symbol, chain, wallet, engine_notional,
      exec_min, exec_max, sleep)
    """

    symbol: str = "SOL/USDC"
    chain: str = "solana"
    wallet_id: str = "sniper_sol"

    # Notional vu par la STRATÉGIE (gros pour passer les filtres éventuellement)
    engine_notional_usd: Decimal = Decimal("200")

    # Notionnels réellement exécutés (profil LIVE_150)
    exec_min_notional_usd: Decimal = Decimal("2")
    exec_max_notional_usd: Decimal = Decimal("6")

    # Intervalle de tick runtime (secondes)
    sleep_seconds: float = 5.0


# ---------------------------------------------------------------------------
# Provider de prix (abstraction QuickNode / DEX / autre)
# ---------------------------------------------------------------------------

class PriceProvider(Protocol):
    """Provider de prix générique pour le runtime memecoin.

    Implémentation typique : wrapper QuickNode / agrégateur DEX.
    Le format exact du dict retourné est libre, il est simplement
    transmis tel quel à ExecutionEngine.execute_signal(..., prices=...).
    """

    def get_prices(
        self,
        *,
        symbol: str,
        chain: str,
        wallet_id: Optional[str] = None,
    ) -> Dict[str, Decimal]:
        """Retourne un mapping de prix (ex: {"mid": Decimal("..."), ...})."""
        ...


# ---------------------------------------------------------------------------
# Objet runtime (boucle principale)
# ---------------------------------------------------------------------------

class MemecoinRuntime:
    """Colle runtime pour la strat memecoin en mode PAPER_ONCHAIN (M10).

    Chaîne de responsabilité :

      config.json
        -> RuntimeWalletManager
        -> PaperTrader
        -> ExecutionEngine
        -> MemecoinStrategyEngine

    Cette classe NE fait que de la compta / routing interne.
    Aucune TX on-chain réelle n'est envoyée ici.
    """

    def __init__(
        self,
        *,
        raw_config: Dict[str, Any],
        runtime_config: MemecoinRuntimeConfig,
        wallet_manager: RuntimeWalletManager,
        execution_engine: ExecutionEngine,
        memecoin_engine: MemecoinStrategyEngine,
        price_provider: Optional[PriceProvider] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self.raw_config = raw_config
        self.config = runtime_config
        self.wallet_manager = wallet_manager
        self.execution_engine = execution_engine
        self.memecoin_engine = memecoin_engine
        self.price_provider = price_provider
        self.log = logger_ or _log
        self._iteration = 0

    # ------------- Overrides CLI helpers -------------

    def apply_namespace_overrides(self, ns: Any) -> None:
        """Applique les overrides provenant d'un argparse.Namespace.

        Tolérant: ignore les attributs manquants.
        """
        if ns is None:
            return

        # symbol / chain / wallet
        sym = getattr(ns, "symbol", None)
        if sym:
            self.config.symbol = str(sym)

        chain = getattr(ns, "chain", None)
        if chain:
            self.config.chain = str(chain)

        wallet = getattr(ns, "wallet", None)
        if wallet:
            self.config.wallet_id = str(wallet)

        # notionals
        eng_not = getattr(ns, "engine_notional", None)
        if eng_not is not None:
            try:
                self.config.engine_notional_usd = Decimal(str(eng_not))
            except Exception:
                self.log.exception(
                    "MemecoinRuntime: impossible de parser engine_notional=%r",
                    eng_not,
                )

        exec_min = getattr(ns, "exec_min", None)
        if exec_min is not None:
            try:
                self.config.exec_min_notional_usd = Decimal(str(exec_min))
            except Exception:
                self.log.exception(
                    "MemecoinRuntime: impossible de parser exec_min=%r",
                    exec_min,
                )

        exec_max = getattr(ns, "exec_max", None)
        if exec_max is not None:
            try:
                self.config.exec_max_notional_usd = Decimal(str(exec_max))
            except Exception:
                self.log.exception(
                    "MemecoinRuntime: impossible de parser exec_max=%r",
                    exec_max,
                )

        slp = getattr(ns, "sleep", None)
        if slp is not None:
            try:
                self.config.sleep_seconds = float(slp)
            except Exception:
                self.log.exception(
                    "MemecoinRuntime: impossible de parser sleep=%r",
                    slp,
                )

    # ------------- Tick loop -------------

    def _fetch_signals(self) -> Sequence[Any]:
        """Récupère les signaux memecoin pour ce tick.

        On privilégie next_signals(), mais on garde generate_signals()
        en fallback pour compat.
        """
        engine = self.memecoin_engine

        if hasattr(engine, "next_signals"):
            return engine.next_signals()  # type: ignore[no-any-return]

        if hasattr(engine, "generate_signals"):
            return engine.generate_signals()  # type: ignore[no-any-return]

        self.log.error(
            "memecoin_engine n'a ni next_signals() ni generate_signals(). "
            "Aucun trade ne sera généré pour ce tick."
        )
        return []

    def run_once(self) -> int:
        """Exécute un tick de runtime.

        1) refresh_balances() sur RuntimeWalletManager (si dispo)
        2) on_tick() sur RuntimeWalletManager (fees, flows, snapshots…)
        3) next_signals() sur MemecoinStrategyEngine
        2bis) récupération des prix via PriceProvider (si configuré)
        4) exécution de chaque signal via ExecutionEngine
        5) on_tick() sur MemecoinStrategyEngine (hook no-op pour l'instant)

        Retourne le nombre de signaux exécutés.
        """
        self._iteration += 1

        # 0) Optionnel : refresh_balances
        if hasattr(self.wallet_manager, "refresh_balances"):
            try:
                self.wallet_manager.refresh_balances()  # type: ignore[call-arg]
            except Exception:
                self.log.exception(
                    "Erreur dans wallet_manager.refresh_balances()"
                )

        # 1) Tick wallets (flows, fees, snapshots)
        if hasattr(self.wallet_manager, "on_tick"):
            try:
                self.wallet_manager.on_tick()  # type: ignore[call-arg]
            except Exception:
                self.log.exception("Erreur dans wallet_manager.on_tick()")

        # 2) Générer les signaux memecoin (ENTRY + EXIT)
        try:
            signals = list(self._fetch_signals())
        except Exception:
            self.log.exception(
                "Erreur lors de la génération des signaux memecoin"
            )
            signals = []

        # 2bis) Récupérer les prix spot via PriceProvider (optionnel)
        prices: Optional[Dict[str, Decimal]] = None
        if self.price_provider is not None:
            try:
                prices = self.price_provider.get_prices(
                    symbol=self.config.symbol,
                    chain=self.config.chain,
                    wallet_id=self.config.wallet_id,
                )
            except Exception:
                self.log.exception(
                    "Erreur lors de price_provider.get_prices(symbol=%s, chain=%s, wallet_id=%s)",
                    self.config.symbol,
                    self.config.chain,
                    self.config.wallet_id,
                )
                prices = None

        # 3) Exécuter les signaux via ExecutionEngine
        executed = 0
        for sig in signals:
            try:
                self.execution_engine.execute_signal(sig, prices=prices)
                executed += 1
            except Exception:
                self.log.exception(
                    "Erreur lors de execution_engine.execute_signal(signal=%r)",
                    sig,
                )

        # 4) Hook on_tick() sur la stratégie (si disponible)
        if hasattr(self.memecoin_engine, "on_tick"):
            try:
                self.memecoin_engine.on_tick()  # type: ignore[call-arg]
            except Exception:
                self.log.exception("Erreur dans memecoin_engine.on_tick()")

        self.log.info(
            "Tick memecoin #%d — %d signaux générés, %d exécutés.",
            self._iteration,
            len(signals),
            executed,
        )
        return executed

    def run_forever(self, *, max_ticks: Optional[int] = None) -> None:
        """Boucle infinie (ou limitée par max_ticks) pour le runtime memecoin."""
        tick = 0
        sleep_s = float(self.config.sleep_seconds)

        self.log.info(
            "Boucle MemecoinRuntime démarrée (sleep=%.2fs, max_ticks=%s)",
            sleep_s,
            str(max_ticks),
        )

        try:
            while True:
                tick += 1
                if max_ticks is not None and tick > max_ticks:
                    self.log.info(
                        "max_ticks=%d atteint, arrêt du runtime.",
                        max_ticks,
                    )
                    break

                self.run_once()
                time.sleep(sleep_s)

        except KeyboardInterrupt:
            self.log.info(
                "Interruption utilisateur (Ctrl+C), arrêt du runtime memecoin."
            )
        except Exception:
            self.log.exception(
                "Erreur fatale dans MemecoinRuntime.run_forever()."
            )
            raise


# ---------------------------------------------------------------------------
# Factory principale: build_default_runtime
# ---------------------------------------------------------------------------

def _build_runtime_config_from_global(cfg: Dict[str, Any]) -> MemecoinRuntimeConfig:
    """Initialise une MemecoinRuntimeConfig à partir de config.json.

    - strategies.memecoin_farming.pairs[0]
    - RUNTIME_TICK_INTERVAL_SECONDS (si présent)
    """
    rt_cfg = MemecoinRuntimeConfig()

    try:
        strategies = cfg.get("strategies") or {}
        meme_cfg = strategies.get("memecoin_farming") or {}
        pairs = meme_cfg.get("pairs") or []
        if pairs:
            p0 = pairs[0]
            rt_cfg.symbol = str(p0.get("symbol", rt_cfg.symbol))
            rt_cfg.chain = str(p0.get("chain", rt_cfg.chain))
            rt_cfg.wallet_id = str(p0.get("wallet_id", rt_cfg.wallet_id))

            # min/max notionals d'exécution
            from decimal import Decimal as _D

            if "min_notional_usd" in p0:
                rt_cfg.exec_min_notional_usd = _D(str(p0["min_notional_usd"]))
            if "max_notional_usd" in p0:
                rt_cfg.exec_max_notional_usd = _D(str(p0["max_notional_usd"]))

        # tick interval global (optionnel)
        raw_sleep = cfg.get("RUNTIME_TICK_INTERVAL_SECONDS")
        if raw_sleep is not None:
            rt_cfg.sleep_seconds = float(raw_sleep)

    except Exception:
        _log.exception(
            "_build_runtime_config_from_global: impossible d'extraire la "
            "config memecoin_farming depuis config.json, "
            "valeurs par défaut conservées."
        )

    return rt_cfg


def build_default_runtime(*args: Any, **kwargs: Any) -> MemecoinRuntime:
    """Factory principale utilisée par scripts/run_m10_memecoin_runtime.py.

    On reste TRES tolérant sur la signature:

      runtime = build_default_runtime()
      runtime = build_default_runtime(logger_=logger)
      runtime = build_default_runtime(args_namespace, logger_=logger)
      runtime = build_default_runtime(args_namespace, logger_=logger, price_provider=provider)
    """
    # 1) Config + logging global
    cfg = load_config()
    setup_logging_from_config(cfg)

    logger_: Optional[logging.Logger] = kwargs.get("logger_") or kwargs.get("logger")
    price_provider: Optional[PriceProvider] = kwargs.get("price_provider")
    log = logger_ or _log

    # 2) Builders
    runtime_wallet_manager = build_runtime_wallet_manager(cfg, logger_=log)
    exec_engine = build_execution_engine(runtime_wallet_manager, logger_=log)
    memecoin_engine = build_memecoin_engine(cfg, logger_=log)

    # 3) RuntimeConfig initiale depuis config.json
    rt_cfg = _build_runtime_config_from_global(cfg)

    # 4) Overrides éventuels via args[0] (Namespace)
    ns = args[0] if args else None

    runtime = MemecoinRuntime(
        raw_config=cfg,
        runtime_config=rt_cfg,
        wallet_manager=runtime_wallet_manager,
        execution_engine=exec_engine,
        memecoin_engine=memecoin_engine,
        price_provider=price_provider,
        logger_=log,
    )

    runtime.apply_namespace_overrides(ns)

    log.info(
        "MemecoinRuntime construit (symbol=%s, chain=%s, wallet_id=%s, "
        "exec_min=%s, exec_max=%s, sleep=%.2fs, price_provider=%s)",
        runtime.config.symbol,
        runtime.config.chain,
        runtime.config.wallet_id,
        str(runtime.config.exec_min_notional_usd),
        str(runtime.config.exec_max_notional_usd),
        runtime.config.sleep_seconds,
        "ON" if price_provider is not None else "OFF",
    )

    return runtime


__all__ = [
    "MemecoinRuntimeConfig",
    "PriceProvider",
    "MemecoinRuntime",
    "build_default_runtime",
]

