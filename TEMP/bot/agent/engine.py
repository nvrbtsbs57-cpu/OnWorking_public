from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Deque

import time
import json
from collections import deque
from urllib import request as urllib_request
from urllib.error import URLError, HTTPError

from bot.core.logging import get_logger
from bot.trading.execution import (
    ExecutionEngine as PaperExecutionEngine,  # moteur PAPER (bot.trading.execution)
)
from bot.trading.models import TradeSide, Signal, SignalSource
from bot.trading.paper_trader import PaperTraderConfig, PaperTrader  # PaperTrader pas utilisé ici, mais dispo si besoin

logger = get_logger(__name__)


# ======================================================================
# Config AgentEngine
# ======================================================================


@dataclass
class AgentEngineConfig:
    """
    Configuration haut niveau de l'AgentEngine.

    Elle est construite par scripts/start_bot.py via AgentEngineConfig.from_dict()
    à partir d'un dict de ce type :

        {
          "agent_mode": "paper" / "godmode",
          "safety_mode": "SAFE" / "DEGEN" / ...,
          "events": {
            "url": "http://127.0.0.1:8000/godmode/events",
            "poll_interval_seconds": 1.0,
            "timeout_seconds": 10.0,
          },
          "events_backoff": {
            "max_consecutive_failures": 10,
            "initial_delay_seconds": 1.0,
            "max_delay_seconds": 60.0,
          },
          "execution": {
            "enabled": false,
          },
          "alerts": {...},
          "orderflow_alerts": {...},

          // RÈGLES PAPER
          "min_notional_usd": 10.0,
          "per_market_notional_usd": {
            "ethereum:eth": 50.0,
            "solana:sol": 25.0,
            "eth": 50.0
          },
          "allowed_event_types": ["entry", "signal"],

          // GARDE-FOUS
          "max_trades_per_minute_global": 60,
          "max_trades_per_minute_per_market": 10
        }
    """

    # mode lisible (paper / godmode / etc.)
    agent_mode: str = "paper"
    safety_mode: str = "SAFE"

    # section events
    events_url: str = ""
    events_poll_interval_seconds: float = 1.0
    events_timeout_seconds: float = 10.0

    # backoff
    events_backoff_max_consecutive_failures: int = 10
    events_backoff_initial_delay_seconds: float = 1.0
    events_backoff_max_delay_seconds: float = 60.0

    # exécution (LIVE, pas PAPER)
    execution_enabled: bool = False

    # règles de sizing / filtrage pour le PAPER
    # (0 => pas de min, dict/set vides => désactivé)
    min_notional_usd: Decimal = Decimal("0")
    per_market_notional_usd: Dict[str, Decimal] = field(default_factory=dict)
    allowed_event_types: Optional[set[str]] = None  # ex: {"entry", "signal"}

    # garde-fous de débit PAPER (0 => pas de limite)
    max_trades_per_minute_global: int = 0
    max_trades_per_minute_per_market: int = 0

    # configs bruts supplémentaires (non utilisés pour l'instant)
    alerts_config: Dict[str, Any] = field(default_factory=dict)
    orderflow_alerts_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentEngineConfig":
        """
        Construit la config à partir du dict préparé dans start_bot.py.
        """
        if d is None:
            d = {}

        agent_mode = str(d.get("agent_mode", "paper"))
        safety_mode = str(d.get("safety_mode", "SAFE"))

        events_cfg = d.get("events", {}) or {}
        events_url = str(events_cfg.get("url", ""))
        try:
            poll_interval = float(events_cfg.get("poll_interval_seconds", 1.0))
        except Exception:
            poll_interval = 1.0
        try:
            timeout_seconds = float(events_cfg.get("timeout_seconds", 10.0))
        except Exception:
            timeout_seconds = 10.0

        backoff_cfg = d.get("events_backoff", {}) or {}
        max_failures = int(backoff_cfg.get("max_consecutive_failures", 10))
        try:
            backoff_initial = float(backoff_cfg.get("initial_delay_seconds", 1.0))
        except Exception:
            backoff_initial = 1.0
        try:
            backoff_max = float(backoff_cfg.get("max_delay_seconds", 60.0))
        except Exception:
            backoff_max = 60.0

        exec_cfg = d.get("execution", {}) or {}
        execution_enabled = bool(exec_cfg.get("enabled", False))

        # ------------------------------------------------------------------
        # Règles PAPER (sizing / filtrage) optionnelles
        # ------------------------------------------------------------------
        try:
            min_notional_usd = Decimal(str(d.get("min_notional_usd", "0")))
        except Exception:
            min_notional_usd = Decimal("0")

        per_market_notional_usd: Dict[str, Decimal] = {}
        per_market_cfg_raw = d.get("per_market_notional_usd", {}) or {}
        if isinstance(per_market_cfg_raw, dict):
            for k, v in per_market_cfg_raw.items():
                try:
                    key = str(k).lower()
                    per_market_notional_usd[key] = Decimal(str(v))
                except Exception:
                    logger.debug(
                        "AgentEngineConfig: per_market_notional_usd invalide (%s=%s), ignoré",
                        k,
                        v,
                    )

        allowed_event_types_raw = d.get("allowed_event_types")
        if isinstance(allowed_event_types_raw, (list, tuple, set)):
            allowed_event_types = {str(x).lower() for x in allowed_event_types_raw}
        else:
            allowed_event_types = None

        try:
            max_trades_per_minute_global = int(
                d.get("max_trades_per_minute_global", 0) or 0
            )
        except Exception:
            max_trades_per_minute_global = 0

        try:
            max_trades_per_minute_per_market = int(
                d.get("max_trades_per_minute_per_market", 0) or 0
            )
        except Exception:
            max_trades_per_minute_per_market = 0

        alerts_cfg = d.get("alerts", {}) or {}
        of_alerts_cfg = d.get("orderflow_alerts", {}) or {}

        return cls(
            agent_mode=agent_mode,
            safety_mode=safety_mode,
            events_url=events_url,
            events_poll_interval_seconds=poll_interval,
            events_timeout_seconds=timeout_seconds,
            events_backoff_max_consecutive_failures=max_failures,
            events_backoff_initial_delay_seconds=backoff_initial,
            events_backoff_max_delay_seconds=backoff_max,
            execution_enabled=execution_enabled,
            min_notional_usd=min_notional_usd,
            per_market_notional_usd=per_market_notional_usd,
            allowed_event_types=allowed_event_types,
            max_trades_per_minute_global=max_trades_per_minute_global,
            max_trades_per_minute_per_market=max_trades_per_minute_per_market,
            alerts_config=alerts_cfg,
            orderflow_alerts_config=of_alerts_cfg,
        )


# ======================================================================
# AgentEngine : consomme /events -> Signals PAPER
# ======================================================================


class AgentEngine:
    """
    AgentEngine pour BOT_GODMODE (version simplifiée).

    - consomme périodiquement l'endpoint /events
    - tente de transformer chaque event en Signal papier simple
    - envoie ces Signals au PaperExecutionEngine (PaperTrader -> TradeStore)
    """

    def __init__(
        self,
        config: AgentEngineConfig,
        alert_engine: Optional[Any] = None,
        watchlist_wallet_manager: Optional[Any] = None,
        trade_wallet_manager: Optional[Any] = None,
        execution_engine: Optional[Any] = None,          # moteur LIVE éventuel (non utilisé ici)
        paper_execution_engine: Optional[PaperExecutionEngine] = None,
        risk_engine: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.alert_engine = alert_engine
        self.watchlist_wallet_manager = watchlist_wallet_manager
        self.trade_wallet_manager = trade_wallet_manager
        self.execution_engine = execution_engine
        self.risk_engine = risk_engine

        # état simple pour gérer le backoff
        self._consecutive_failures: int = 0
        self._stopped: bool = False

        # garde-fous : mémoire courte des trades papier (fenêtre glissante 60s)
        self._recent_trades_global: Deque[float] = deque()
        self._recent_trades_per_market: Dict[str, Deque[float]] = {}

        # ------------------------------------------------------------------
        # Moteur PAPER : à injecter par start_bot / runtime.
        # ------------------------------------------------------------------
        self.paper_execution_engine: Optional[PaperExecutionEngine] = (
            paper_execution_engine
        )

        if self.paper_execution_engine is None:
            logger.warning(
                "AgentEngine: aucun moteur PAPER injecté (paper_execution_engine=None). "
                "Les events seront consommés mais NON exécutés tant qu'un moteur "
                "d'exécution papier n'est pas fourni."
            )

        logger.info(
            "AgentEngine initialisé (mode=%s, safety=%s, events_url=%s, execution_enabled=%s)",
            self.config.agent_mode,
            self.config.safety_mode,
            self.config.events_url,
            self.config.execution_enabled,
        )

    # ------------------------------------------------------------------
    # Arrêt propre
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Permet un arrêt propre de la boucle run_forever()."""
        self._stopped = True

    # ------------------------------------------------------------------
    # Fetch + parsing des events (urllib)
    # ------------------------------------------------------------------

    def _fetch_events(self) -> List[Dict[str, Any]]:
        """
        Appelle l'API /events et renvoie une liste de dicts.
        Utilise uniquement la stdlib (urllib).
        """
        if not self.config.events_url:
            return []

        req = urllib_request.Request(self.config.events_url, method="GET")

        try:
            with urllib_request.urlopen(
                req, timeout=self.config.events_timeout_seconds or 10
            ) as resp:
                status = getattr(resp, "status", None)
                if status and status >= 400:
                    raise HTTPError(
                        self.config.events_url,
                        status,
                        "HTTP error",
                        hdrs=resp.headers,
                        fp=None,
                    )

                raw = resp.read().decode("utf-8", errors="replace")
        except (URLError, HTTPError) as exc:
            logger.warning(
                "AgentEngine: erreur réseau sur events_url=%s: %s",
                self.config.events_url,
                exc,
            )
            raise
        except Exception as exc:
            logger.exception("AgentEngine: erreur inattendue sur _fetch_events: %s", exc)
            raise

        if not raw:
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "AgentEngine: réponse /events non JSON, ignorée (taille=%d).",
                len(raw),
            )
            return []

        # formats supportés :
        # - liste d'events
        # - { "events": [...] } ou { "items": [...] }
        # - un seul event {}
        if isinstance(data, list):
            return [ev for ev in data if isinstance(ev, dict)]

        if isinstance(data, dict):
            if "events" in data and isinstance(data["events"], list):
                return [ev for ev in data["events"] if isinstance(ev, dict)]
            if "items" in data and isinstance(data["items"], list):
                return [ev for ev in data["items"] if isinstance(ev, dict)]
            return [data]

        logger.debug("AgentEngine: format de réponse /events non supporté: %r", data)
        return []

    # ------------------------------------------------------------------
    # Construction des Signals PAPER à partir des events
    # ------------------------------------------------------------------

    def _build_signal_from_event(self, event: Dict[str, Any]) -> Optional[Signal]:
        """
        Essaie de convertir un event en Signal pour le moteur PAPER.

        Applique aussi les règles simples de filtrage / sizing configurées
        sur AgentEngineConfig (min_notional_usd, per_market_notional_usd,
        allowed_event_types, etc.).
        """

        # ---- chaine ----
        chain = (
            event.get("chain")
            or event.get("network")
            or event.get("chain_id")
            or "ethereum"
        )
        chain_str = str(chain)

        # ---- symbole ----
        symbol = (
            event.get("symbol")
            or event.get("token_symbol")
            or event.get("asset")
            or "ETH"
        )
        symbol_str = str(symbol)

        market_key = f"{chain_str.lower()}:{symbol_str.lower()}"

        # ---- notionnel USD (event brut) ----
        notional_raw = (
            event.get("notional_usd")
            or event.get("usd_notional")
            or event.get("notional")
            or event.get("amount_usd")
            or event.get("value_usd")
        )
        if notional_raw is None:
            logger.debug("Event sans notional_usd, ignoré: %s", str(event)[:200])
            return None

        try:
            notional = Decimal(str(notional_raw))
        except Exception:
            logger.debug(
                "Event notional non convertible (notional=%s), ignoré",
                notional_raw,
            )
            return None

        if notional <= 0:
            logger.debug(
                "Event notional <= 0 (notional=%s), ignoré",
                notional,
            )
            return None

        # ---- override per-market éventuel ----
        override_notional = None
        if self.config.per_market_notional_usd:
            # clé la plus spécifique : "<chain>:<symbol>"
            override_notional = self.config.per_market_notional_usd.get(market_key)
            # fallback possible juste sur le symbole
            if override_notional is None:
                override_notional = self.config.per_market_notional_usd.get(
                    symbol_str.lower()
                )

        if override_notional is not None and override_notional > 0:
            logger.debug(
                "AgentEngine: override notional pour %s via per_market_notional_usd "
                "(event=%s -> override=%s).",
                market_key,
                notional,
                override_notional,
            )
            notional = override_notional

        # ---- min_notional_usd global ----
        if self.config.min_notional_usd and self.config.min_notional_usd > 0:
            if notional < self.config.min_notional_usd:
                logger.info(
                    "AgentEngine: event filtré car notional < min_notional_usd "
                    "(chain=%s, symbol=%s, notional=%s < %s).",
                    chain_str,
                    symbol_str,
                    notional,
                    self.config.min_notional_usd,
                )
                return None

        # ---- side (BUY / SELL) ----
        side_str: Optional[str] = (
            event.get("side")
            or event.get("direction")
            or event.get("order_side")
        )

        if side_str is None:
            if event.get("is_buy") is True:
                side_str = "buy"
            elif event.get("is_sell") is True:
                side_str = "sell"

        if side_str is None:
            logger.debug("Event sans side, ignoré: %s", str(event)[:200])
            return None

        side_norm = str(side_str).lower()
        if side_norm in ("buy", "long", "bid"):
            side = TradeSide.BUY
        elif side_norm in ("sell", "short", "ask"):
            side = TradeSide.SELL
        else:
            logger.debug(
                "Event side inconnu (%s), ignoré: %s",
                side_str,
                str(event)[:200],
            )
            return None

        # ---- prix d'entrée éventuel ----
        price_raw = (
            event.get("price")
            or event.get("avg_price")
            or event.get("entry_price")
        )
        entry_price: Optional[Decimal] = None
        if price_raw is not None:
            try:
                entry_price = Decimal(str(price_raw))
            except Exception:
                entry_price = None

        # ---- type d'event / filtrage ----
        event_type = (
            event.get("type")
            or event.get("event_type")
            or event.get("category")
            or "unknown"
        )
        event_type_str = str(event_type)

        allowed = self.config.allowed_event_types
        if allowed:
            if event_type_str.lower() not in allowed:
                logger.debug(
                    "AgentEngine: event ignoré car type '%s' non dans allowed_event_types %s.",
                    event_type_str,
                    sorted(allowed),
                )
                return None

        # ---- meta / wallet_id / tags ----
        meta: Dict[str, Any] = {}
        if isinstance(event.get("meta"), dict):
            meta.update(event["meta"])

        # wallet_id si présent dans l'event
        wallet_id = (
            event.get("wallet_id")
            or event.get("wallet")
            or event.get("wallet_name")
        )
        if wallet_id is not None:
            meta["wallet_id"] = wallet_id

        meta["raw_event"] = event
        meta.setdefault("source", "agent_events")
        meta.setdefault("event_type", event_type_str)
        meta.setdefault("market_key", market_key)
        meta.setdefault("strategy_tag", f"events:{event_type_str}")

        # strategy_id optionnel
        strategy_id = event.get("strategy_id")

        # confidence optionnelle
        confidence_val = 1.0
        if "confidence" in event:
            try:
                confidence_val = float(event.get("confidence", 1.0))
            except Exception:
                confidence_val = 1.0

        # ---- Construction du Signal ----
        signal = Signal(
            chain=chain_str,
            symbol=symbol_str,
            side=side,
            size_usd=notional,
            token_address=event.get("token_address"),
            entry_price=entry_price,
            leverage=None,
            strategy_id=strategy_id,
            source=SignalSource.WEBHOOK,
            confidence=confidence_val,
            meta=meta,
        )
        return signal

    def _get_market_key_from_signal(self, signal: Signal) -> str:
        chain = (signal.chain or "").lower()
        symbol = (signal.symbol or "").lower()
        return f"{chain}:{symbol}"

    def _check_rate_limits(self, market_key: str, now: Optional[float] = None) -> bool:
        """
        Retourne True si un trade peut encore être exécuté, False sinon.
        Applique les limites globales et par marché sur une fenêtre de 60s.
        """
        if now is None:
            now = time.time()

        window = 60.0  # une minute glissante
        max_global = self.config.max_trades_per_minute_global
        max_per_market = self.config.max_trades_per_minute_per_market

        # Limite globale
        if max_global and max_global > 0:
            dq = self._recent_trades_global
            while dq and now - dq[0] > window:
                dq.popleft()
            if len(dq) >= max_global:
                logger.info(
                    "AgentEngine: limite globale de %d trades/min atteinte, requête ignorée.",
                    max_global,
                )
                return False

        # Limite par marché
        if max_per_market and max_per_market > 0:
            dq_market = self._recent_trades_per_market.setdefault(
                market_key, deque()
            )
            while dq_market and now - dq_market[0] > window:
                dq_market.popleft()
            if len(dq_market) >= max_per_market:
                logger.info(
                    "AgentEngine: limite de %d trades/min atteinte pour le marché %s, requête ignorée.",
                    max_per_market,
                    market_key,
                )
                return False

        return True

    def _register_trade_timestamp(self, market_key: str, ts: Optional[float] = None) -> None:
        """
        Enregistre l'horodatage d'un trade pour la limite globale et par marché.
        """
        if ts is None:
            ts = time.time()

        window = 60.0

        # Global
        self._recent_trades_global.append(ts)
        max_global = self.config.max_trades_per_minute_global
        if max_global and max_global > 0:
            dq = self._recent_trades_global
            while dq and ts - dq[0] > window:
                dq.popleft()

        # Par marché
        max_per_market = self.config.max_trades_per_minute_per_market
        if max_per_market and max_per_market > 0:
            dq_market = self._recent_trades_per_market.setdefault(market_key, deque())
            dq_market.append(ts)
            while dq_market and ts - dq_market[0] > window:
                dq_market.popleft()

    def _process_events(self, events: List[Dict[str, Any]]) -> None:
        """
        Transforme chaque event en Signal et l'envoie au moteur PAPER.

        Applique également les garde-fous de débit (max trades/minute).
        """
        if not events:
            return
        if self.paper_execution_engine is None:
            logger.debug(
                "Events reçus mais paper_execution_engine is None, rien à faire."
            )
            return

        for ev in events:
            try:
                signal = self._build_signal_from_event(ev)
                if signal is None:
                    continue

                market_key = self._get_market_key_from_signal(signal)
                now = time.time()

                if not self._check_rate_limits(market_key, now=now):
                    # Motif loggué dans _check_rate_limits
                    continue

                tag = signal.meta.get("strategy_tag") or signal.meta.get("event_type")

                logger.info(
                    "AgentEngine PAPER: %s %s %s USD sur %s (tag=%s)",
                    signal.side.value.upper(),
                    signal.symbol,
                    str(signal.size_usd),
                    signal.chain,
                    tag,
                )

                self._register_trade_timestamp(market_key, ts=now)

                # Appel à l'API réelle d'exécution PAPER
                self.paper_execution_engine.execute_signal(signal)

            except Exception as exc:
                logger.exception(
                    "Erreur lors du traitement d'un event PAPER: %s",
                    exc,
                )

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        """
        Boucle principale qui poll l'endpoint /events avec un backoff simple.
        """
        logger.info("AgentEngine: run_forever() démarré.")
        delay = self.config.events_poll_interval_seconds or 1.0

        while not self._stopped:
            try:
                events = self._fetch_events()
                self._consecutive_failures = 0  # reset en cas de succès

                if events:
                    logger.debug("AgentEngine: %d events reçус.", len(events))
                    try:
                        self._process_events(events)
                    except Exception as exc:
                        logger.exception(
                            "AgentEngine: erreur lors du traitement des events: %s",
                            exc,
                        )

                # pause simple entre deux polls
                time.sleep(delay)

            except (URLError, HTTPError) as exc:
                self._consecutive_failures += 1
                logger.warning(
                    "AgentEngine: échec réseau (%s). consecutive_failures=%d",
                    exc,
                    self._consecutive_failures,
                )

                # backoff exponentiel borné
                backoff_initial = self.config.events_backoff_initial_delay_seconds or 1.0
                backoff_max = self.config.events_backoff_max_delay_seconds or 60.0
                n = min(self._consecutive_failures, 10)
                delay = min(backoff_initial * (2 ** (n - 1)), backoff_max)

                logger.info("AgentEngine: backoff de %.1fs avant le prochain poll.", delay)
                time.sleep(delay)

            except Exception as exc:
                self._consecutive_failures += 1
                logger.exception(
                    "AgentEngine: erreur inattendue dans la boucle principale: %s",
                    exc,
                )
                time.sleep(1.0)

        logger.info("AgentEngine: run_forever() arrêté proprement.")

