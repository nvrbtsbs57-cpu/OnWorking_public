from __future__ import annotations

from bot.agent.modes import ModeConfig, SafetyMode  # SAFE / NORMAL / DEGEN
from bot.agent.position_profiles import build_position_config_for_safety_mode
from bot.trading.positions import PositionConfig, PositionManager, PositionManagerConfig

import json
import time
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from bot.core.logging import get_logger
from bot.trading.wallets import WalletManager as WatchlistWalletManager
from bot.signals import (
    SignalContext,
    SignalFeature,
    RawSignal,
    SignalSide,
    ScoredSignal,
)

# Nouveau : wallet/execution pour le trading multi-chain
from bot.wallet.manager import WalletManager as TradeWalletManager
from bot.execution.engine import (
    ExecutionEngine,
    ExecutionRequest,
    OrderSide,
    OrderType,
)

# ExecutionEngine PAPER + positions (Module 6)
from bot.trading.execution import (
    ExecutionEngine as PaperExecutionEngine,
    ExecutionRequest as PaperExecutionRequest,
)
from bot.trading.models import TradeSide

# Nouveau : intégration RiskEngine module 6 (best-effort)
try:
    from bot.core.risk import RiskDecision, OrderContext  # type: ignore
except ImportError:
    RiskDecision = None  # type: ignore
    OrderContext = None  # type: ignore

logger = get_logger(__name__)


# ======================================================================
# Config AgentEngine
# ======================================================================


@dataclass
class AgentEngineConfig:
    """
    Wrapper autour de la config passée par start_bot.

    On en extrait ce dont on a besoin :

    - agent_mode : paper / live / godmode...
    - safety_mode : SAFE / NORMAL / DEGEN
    - endpoint API events
    - paramètres de backoff / polling
    """

    agent_mode: str
    safety_mode: SafetyMode

    events_url: str
    events_poll_interval: float = 3.0
    events_timeout_seconds: float = 10.0

    # Backoff
    max_consecutive_failures: int = 10
    backoff_initial_delay: float = 1.0
    backoff_max_delay: float = 60.0

    # "garde-fou" pour les events whale/orderflow
    whale_alert_threshold_usd: float = 50_000.0
    orderflow_notional_threshold_usd: float = 50_000.0
    orderflow_imbalance_threshold_pct: float = 0.8  # 80%

    # Taille max des batches d'events traités par poll
    max_events_per_poll: int = 1_000

    # nouveau: enable/disable execution
    execution_enabled: bool = False

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AgentEngineConfig":
        # On récupère le mode agent et safety_mode
        agent_mode = str(d.get("agent_mode") or "paper").lower()

        safety_mode_raw = (d.get("safety_mode") or "SAFE").upper()
        try:
            safety_mode = SafetyMode[safety_mode_raw]
        except KeyError:
            safety_mode = SafetyMode.SAFE

        # Config events
        events_cfg = d.get("events") or {}
        events_url = str(events_cfg.get("url") or "http://localhost:8000/events")
        events_poll_interval = float(events_cfg.get("poll_interval_seconds") or 3.0)
        events_timeout_seconds = float(events_cfg.get("timeout_seconds") or 10.0)

        # Backoff
        backoff_cfg = d.get("events_backoff") or {}
        max_consecutive_failures = int(
            backoff_cfg.get("max_consecutive_failures") or 10
        )
        backoff_initial_delay = float(backoff_cfg.get("initial_delay_seconds") or 1.0)
        backoff_max_delay = float(backoff_cfg.get("max_delay_seconds") or 60.0)

        # Alerte whales
        alerts_cfg = d.get("alerts") or {}
        whale_threshold = float(
            alerts_cfg.get("whale_alert_threshold_usd") or 50_000.0
        )

        # Orderflow
        of_cfg = d.get("orderflow_alerts") or {}
        of_notional = float(of_cfg.get("notional_threshold_usd") or whale_threshold)
        of_imbalance = float(of_cfg.get("imbalance_threshold_pct") or 0.8)

        # Execution
        exec_cfg = d.get("execution") or {}
        execution_enabled = bool(exec_cfg.get("enabled") or False)

        return AgentEngineConfig(
            agent_mode=agent_mode,
            safety_mode=safety_mode,
            events_url=events_url,
            events_poll_interval=events_poll_interval,
            events_timeout_seconds=events_timeout_seconds,
            max_consecutive_failures=max_consecutive_failures,
            backoff_initial_delay=backoff_initial_delay,
            backoff_max_delay=backoff_max_delay,
            whale_alert_threshold_usd=whale_threshold,
            orderflow_notional_threshold_usd=of_notional,
            orderflow_imbalance_threshold_pct=of_imbalance,
            execution_enabled=execution_enabled,
        )


# ======================================================================
# State
# ======================================================================


@dataclass
class AgentEngineState:
    """
    Etat interne de l'AgentEngine (utile pour diagnostics & dashboard).
    """

    is_running: bool = False
    last_event_ts: Optional[float] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    backoff_delay: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


# ======================================================================
# AgentEngine
# ======================================================================


class AgentEngine:
    """
    AgentEngine : boucle principale qui poll l'API `/events` et génère
    des signaux / ordres, en fonction du mode.

    - Récupère les events normalisés
    - Applique des règles simples pour déclencher des signaux (whales, orderflow extrême)
    - Intègre ensuite SignalEngine, RiskEngine, ExecutionEngine...
    """

    def __init__(
        self,
        config: AgentEngineConfig,
        alert_engine: Optional[Any] = None,
        signal_engine: Optional[Any] = None,
        watchlist_wallet_manager: Optional[WatchlistWalletManager] = None,
        trade_wallet_manager: Optional[TradeWalletManager] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        paper_execution_engine: Optional[PaperExecutionEngine] = None,
        risk_engine: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.alert_engine = alert_engine
        self.signal_engine = signal_engine
        self.watchlist_wallet_manager = watchlist_wallet_manager
        self.trade_wallet_manager = trade_wallet_manager
        self.execution_engine = execution_engine
        self.paper_execution_engine = paper_execution_engine
        self.risk_engine = risk_engine

        self.state = AgentEngineState()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # lazy init pour le paper PositionManager
        self._paper_position_manager: Optional[PositionManager] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_in_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.warning("AgentEngine already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()
        logger.info("AgentEngine démarré en background")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        logger.info("AgentEngine arrêté")

    def run_forever(self) -> None:
        """
        Boucle principale.
        """
        logger.info(
            "AgentEngine: démarrage en mode %s / safety=%s",
            self.config.agent_mode,
            self.config.safety_mode.name,
        )
        self.state.is_running = True

        # hook de démarrage
        try:
            self.on_start()
        except Exception as e:
            logger.debug("Erreur dans on_start hook", exc_info=True)
            self.on_error(e)

        try:
            backoff = 0.0
            while not self._stop_event.is_set():
                try:
                    processed = self._poll_and_process()
                    if processed > 0:
                        backoff = 0.0
                    else:
                        backoff = min(
                            max(self.config.events_poll_interval, backoff + 0.5),
                            self.config.backoff_max_delay,
                        )
                except Exception as e:
                    logger.error(
                        "AgentEngine: exception dans run_forever: %s", e, exc_info=True
                    )
                    self._record_error(str(e))
                    try:
                        self.on_error(e)
                    except Exception:
                        logger.debug(
                            "Erreur dans on_error hook", exc_info=True
                        )

                    backoff = min(
                        max(
                            self.config.backoff_initial_delay,
                            backoff * 2 if backoff > 0 else 1.0,
                        ),
                        self.config.backoff_max_delay,
                    )

                if backoff > 0:
                    self.state.backoff_delay = backoff
                    time.sleep(backoff)
                else:
                    time.sleep(self.config.events_poll_interval)

        finally:
            self.state.is_running = False
            try:
                self.on_stop()
            except Exception:
                logger.debug("Erreur dans on_stop hook", exc_info=True)
            logger.info("AgentEngine: sortie de run_forever")

    # ------------------------------------------------------------------
    # Poll API
    # ------------------------------------------------------------------

    def _poll_and_process(self) -> int:
        """
        Poll l'API /events, renvoie le nombre d'events traités.
        """
        try:
            events = self._fetch_events()
        except Exception as e:
            self._record_error(str(e))
            return 0

        if not events:
            return 0

        try:
            processed = self._handle_events(events)
        except Exception as e:
            logger.error(
                "AgentEngine: erreur lors du traitement des events", exc_info=True
            )
            self._record_error(str(e))
            return 0

        self.state.last_error = None
        self.state.consecutive_failures = 0
        self.state.backoff_delay = 0.0

        return processed

    def _fetch_events(self) -> List[Dict[str, Any]]:
        """
        Fetch sur l'API /events.
        """
        url = self.config.events_url
        timeout = self.config.events_timeout_seconds

        logger.debug("AgentEngine: fetching events depuis %s", url)

        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status} sur {url}")

                raw = resp.read()
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    raise RuntimeError(
                        f"Impossible de parser la réponse JSON: {e}"
                    ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Erreur réseau sur {url}: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Erreur lors de l'appel à {url}: {e}") from e

        if isinstance(data, dict):
            events = data.get("events") or data.get("data") or []
        else:
            events = data

        if not isinstance(events, list):
            raise RuntimeError("Réponse /events invalide: `events` n'est pas une liste")

        return events[: self.config.max_events_per_poll]

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def _handle_events(self, events: List[Dict[str, Any]]) -> int:
        """
        Traite un batch d'events "normalisés" venant de /events.
        """
        if not events:
            return 0

        last_ts = None
        types: Dict[str, int] = {}
        whale_alerts = 0
        of_alerts = 0

        for ev in events:
            if not isinstance(ev, dict):
                continue

            ev_type = str(ev.get("type") or ev.get("kind") or "unknown")
            types[ev_type] = types.get(ev_type, 0) + 1

            ts = ev.get("ts") or ev.get("time") or ev.get("timestamp")
            if ts:
                last_ts = ts

            chain = self._normalize_chain(ev.get("chain") or "unknown")

            if ev_type == "whale_tx":
                whale_alerts += self._handle_whale_event(ev, chain)

            of = ev.get("order_flow")
            if isinstance(of, dict):
                of_alerts += self._handle_order_flow_event(ev, chain, of)

        self.state.last_event_ts = last_ts
        self.state.meta["last_batch_types"] = types

        logger.debug(
            "AgentEngine: batch events stats %s (whale_alerts=%d, of_alerts=%d)",
            types,
            whale_alerts,
            of_alerts,
        )

        return len(events)

    def _handle_whale_event(self, ev: Dict[str, Any], chain: str) -> int:
        """
        Règle d'alerte pour les whales.
        """
        try:
            notional = ev.get("notional_usd") or ev.get("usd_value") or ev.get(
                "amount"
            )
            notional_f = float(notional or 0.0)
        except Exception:
            notional_f = 0.0

        if notional_f < self.config.whale_alert_threshold_usd:
            return 0

        tx_hash = ev.get("tx_hash") or ev.get("transaction_hash") or "unknown"
        token = ev.get("token") or ev.get("token_symbol") or "UNKNOWN"
        sender = ev.get("from") or ev.get("sender") or "??"
        receiver = ev.get("to") or ev.get("receiver") or "??"

        logger.info(
            "Whale alert: chain=%s token=%s notional=%.2f tx=%s",
            chain,
            token,
            notional_f,
            tx_hash,
        )

        if self.alert_engine is not None:
            try:
                self.alert_engine.info(
                    f"Whale {token} {notional_f:,.0f} USD",
                    source="agent_engine",
                    chain=chain,
                    token=token,
                    notional_usd=notional_f,
                    tx_hash=tx_hash,
                    sender=sender,
                    receiver=receiver,
                    threshold_usd=self.config.whale_alert_threshold_usd,
                    extra={
                        "event": "whale_tx",
                        "chain": chain,
                        "token": token,
                        "notional_usd": notional_f,
                        "tx_hash": tx_hash,
                        "sender": sender,
                        "receiver": receiver,
                    },
                )
            except Exception:
                logger.debug("Impossible d'envoyer l'alerte whale", exc_info=True)

        if self.signal_engine is not None:
            try:
                sig = self._build_whale_signal(ev, chain, token, notional_f, tx_hash)
                if sig is not None:
                    scored = self.signal_engine.score_signals([sig])
                    self._maybe_execute_signals(scored)
            except Exception:
                logger.debug(
                    "Erreur lors de la génération de signal whale", exc_info=True
                )

        return 1

    def _handle_order_flow_event(
        self, ev: Dict[str, Any], chain: str, of: Dict[str, Any]
    ) -> int:
        """
        Règle d'alerte pour l'order flow extrême.
        """
        try:
            market = of.get("market") or ev.get("market") or ev.get("symbol")
            if not market:
                return 0

            volume = float(of.get("volume_usd") or of.get("notional_usd") or 0.0)
        except Exception:
            market = None
            volume = 0.0

        if not market or volume <= 0:
            return 0

        if volume < self.config.orderflow_notional_threshold_usd:
            return 0

        imbalance = float(of.get("imbalance") or 0.0)
        if abs(imbalance) < self.config.orderflow_imbalance_threshold_pct:
            return 0

        logger.info(
            "Orderflow alert: chain=%s market=%s volume=%.2f imbalance=%.2f",
            chain,
            market,
            volume,
            imbalance,
        )

        if self.alert_engine is not None:
            try:
                self.alert_engine.info(
                    f"Orderflow {market} {volume:,.0f} USD",
                    source="agent_engine",
                    chain=chain,
                    market=market,
                    volume_usd=volume,
                    imbalance=imbalance,
                    threshold_usd=self.config.orderflow_notional_threshold_usd,
                    threshold_imbalance_pct=self.config.orderflow_imbalance_threshold_pct,
                    extra={
                        "event": "order_flow",
                        "chain": chain,
                        "market": market,
                        "volume_usd": volume,
                        "imbalance": imbalance,
                    },
                )
            except Exception:
                logger.debug(
                    "Impossible d'envoyer l'alerte orderflow", exc_info=True
                )

        if self.signal_engine is not None:
            try:
                sig = self._build_orderflow_signal(
                    ev,
                    chain,
                    market,
                    of,
                    imbalance,
                    volume,
                )
                if sig is not None:
                    scored = self.signal_engine.score_signals([sig])
                    self._maybe_execute_signals(scored)
            except Exception:
                logger.debug(
                    "Erreur lors de la génération de signal orderflow", exc_info=True
                )

        return 1

    # ------------------------------------------------------------------
    # Position / Trading helpers (Module 6 intégration)
    # ------------------------------------------------------------------

    def _build_position_config_for_safety(self) -> PositionConfig:
        """
        Construit un PositionConfig adapté au SafetyMode actuel.
        """
        pos_cfg = build_position_config_for_safety_mode(self.config.safety_mode)

        logger.info(
            "PositionConfig pour safety_mode=%s: %s",
            self.config.safety_mode.name,
            pos_cfg,
        )

        return pos_cfg

    def _ensure_paper_position_manager(self) -> PositionManager:
        """
        Crée (si nécessaire) un PositionManager en mode PAPER.
        """
        if self._paper_position_manager is None:
            pos_cfg = self._build_position_config_for_safety()
            pm_cfg = PositionManagerConfig(
                max_positions=pos_cfg.max_positions,
                max_notional_per_position=pos_cfg.max_notional_per_position,
                max_total_notional=pos_cfg.max_total_notional,
            )
            self._paper_position_manager = PositionManager(config=pm_cfg)

            logger.info(
                "Paper PositionManager initialisé avec config=%s",
                pm_cfg,
            )

        return self._paper_position_manager

    def _paper_execute_order(
        self,
        chain: str,
        symbol: str,
        side: OrderSide,
        notional_usd: float,
        ctx: Optional[SignalContext] = None,
    ) -> None:
        """
        Exécute un ordre "virtuel" via PaperExecutionEngine + PositionManager.
        """
        if self.paper_execution_engine is None:
            logger.debug("PaperExecutionEngine non configuré, skip paper trade")
            return

        pm = self._ensure_paper_position_manager()

        try:
            price = 1.0
            if ctx is not None and hasattr(ctx, "meta") and ctx.meta:
                price_raw = ctx.meta.get("entry_price")
                if price_raw is not None:
                    try:
                        price = float(price_raw)
                    except Exception:
                        price = 1.0

            qty = float(notional_usd) / max(price, 1e-9)

            trade_side = TradeSide.BUY if side == OrderSide.BUY else TradeSide.SELL

            logger.info(
                "Paper trade: chain=%s symbol=%s side=%s notional=%.2f qty=%.6f price=%.6f",
                chain,
                symbol,
                trade_side.value,
                notional_usd,
                qty,
                price,
            )

            req = PaperExecutionRequest(
                chain=chain,
                symbol=symbol,
                side=trade_side,
                qty=Decimal(str(qty)),
                price=Decimal(str(price)),
                notional=Decimal(str(notional_usd)),
                fee=Decimal("0"),
                meta={"context": ctx.to_dict() if ctx is not None else {}},
            )

            trade = self.paper_execution_engine.execute(req, position_manager=pm)

            logger.info(
                "PaperExecutionEngine: trade exécuté: id=%s notional=%s chain=%s",
                trade.id,
                trade.notional,
                trade.chain,
            )
        except Exception as e:
            logger.error("Erreur lors de l'exécution PAPER: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # ExecutionEngine intégration (on-chain stub)
    # ------------------------------------------------------------------

    def _maybe_execute_onchain(
        self,
        chain: str,
        symbol: str,
        side: OrderSide,
        notional_usd: float,
        ctx: Optional[SignalContext] = None,
        wallet_id: Optional[str] = None,
    ) -> None:
        """
        Envoie un ordre vers ExecutionEngine (stub) si configuré.
        """
        if self.execution_engine is None:
            logger.debug("ExecutionEngine non configuré, skip onchain")
            return

        if not self.config.execution_enabled:
            logger.debug(
                "ExecutionEngine configuré mais execution_enabled=false, skip onchain"
            )
            return

        side_str = "buy" if side == OrderSide.BUY else "sell"

        try:
            meta: Dict[str, Any] = {
                "context": ctx.to_dict() if ctx is not None else {}
            }
            if wallet_id is not None:
                meta["wallet_id"] = wallet_id

            req = ExecutionRequest(
                chain=chain,
                market=symbol,
                side=side_str,
                order_type=OrderType.MARKET,
                size_usd=Decimal(str(notional_usd)),
                leverage=Decimal("1"),
                slippage_bps=Decimal("50"),  # 0.5%, à ajuster
                meta=meta,
            )

            logger.info(
                "ExecutionEngine: envoi ordre on-chain chain=%s symbol=%s side=%s size=%.2f wallet=%s",
                chain,
                symbol,
                side_str,
                notional_usd,
                wallet_id,
            )

            self.execution_engine.execute(req)
        except Exception as e:
            logger.error("Erreur lors de l'exécution on-chain: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # SignalEngine integration helpers
    # ------------------------------------------------------------------

    def _build_whale_signal(
        self,
        ev: Dict[str, Any],
        chain: str,
        token: str,
        notional_usd: float,
        tx_hash: str,
    ) -> Optional[RawSignal]:
        """
        Construit un RawSignal à partir d'un event whale_tx.
        """
        symbol = (
            ev.get("symbol")
            or ev.get("market")
            or ev.get("pair")
            or f"{token}-USD"
        )

        norm_chain = self._normalize_chain(chain)

        ctx = SignalContext(
            chain=norm_chain,
            market_type=str(ev.get("market_type") or "spot"),
            base_token=token,
            quote_token=str(ev.get("quote_token") or "USD"),
            venue=str(ev.get("venue") or "unknown"),
            symbol=symbol,
        )

        side_str = str(ev.get("side") or ev.get("direction") or "").lower()
        if "buy" in side_str or "long" in side_str:
            side = SignalSide.LONG
        elif "sell" in side_str or "short" in side_str:
            side = SignalSide.SHORT
        else:
            side = SignalSide.NEUTRAL

        try:
            ratio = notional_usd / max(self.config.whale_alert_threshold_usd, 1.0)
        except Exception:
            ratio = 0.0

        features = [
            SignalFeature(
                name="flow_whale_notional",
                value=Decimal(str(ratio)),
                weight=Decimal("1.0"),
            )
        ]

        entry_price = (
            ev.get("price")
            or ev.get("avg_price")
            or ev.get("mark_price")
            or "0"
        )

        return RawSignal(
            id=f"whale_{tx_hash}",
            created_at=datetime.utcnow(),
            context=ctx,
            side=side,
            source="whales_engine",
            label="whale_tx",
            confidence=Decimal("0.8"),
            features=features,
            meta={
                "tx_hash": tx_hash,
                "entry_price": str(entry_price),
            },
        )

    def _build_orderflow_signal(
        self,
        ev: Dict[str, Any],
        chain: str,
        market: str,
        of: Dict[str, Any],
        imbalance: float,
        volume: float,
    ) -> Optional[RawSignal]:
        """
        Construit un RawSignal à partir d'un event d'order flow extrême.
        """
        base_token = ev.get("base_token") or market
        quote_token = ev.get("quote_token") or "USD"

        norm_chain = self._normalize_chain(chain)

        ctx = SignalContext(
            chain=norm_chain,
            market_type=str(ev.get("market_type") or "perp"),
            base_token=str(base_token),
            quote_token=str(quote_token),
            venue=str(ev.get("venue") or "unknown"),
            symbol=market,
        )

        if imbalance > 0:
            side = SignalSide.LONG
        elif imbalance < 0:
            side = SignalSide.SHORT
        else:
            side = SignalSide.NEUTRAL

        try:
            ratio = volume / max(self.config.orderflow_notional_threshold_usd, 1.0)
        except Exception:
            ratio = 0.0

        features = [
            SignalFeature(
                name="orderflow_notional",
                value=Decimal(str(ratio)),
                weight=Decimal("1.0"),
            ),
            SignalFeature(
                name="orderflow_imbalance",
                value=Decimal(str(abs(imbalance))),
                weight=Decimal("1.0"),
            ),
        ]

        entry_price = (
            ev.get("price")
            or ev.get("avg_price")
            or ev.get("mark_price")
            or "0"
        )

        return RawSignal(
            id=f"of_{market}_{int(time.time())}",
            created_at=datetime.utcnow(),
            context=ctx,
            side=side,
            source="orderflow_engine",
            label="order_flow_extreme",
            confidence=Decimal("0.75"),
            features=features,
            meta={
                "volume_usd": volume,
                "imbalance": imbalance,
                "entry_price": str(entry_price),
            },
        )

    # ------------------------------------------------------------------
    # Mode / safety helpers (AgentEngine "modes")
    # ------------------------------------------------------------------

    def _is_paper_mode(self) -> bool:
        """
        True si l'AgentEngine doit se comporter en mode "paper".
        """
        return self.config.agent_mode in ("paper", "backtest")

    def _is_godmode(self) -> bool:
        """
        True si on est en mode GODMODE (autorise l'exécution réelle).
        """
        return self.config.agent_mode in ("godmode", "live", "prod")

    # ------------------------------------------------------------------
    # Intégration globale : SignalEngine => RiskEngine => ExecutionEngine
    # ------------------------------------------------------------------

    def _maybe_execute_signals(self, scored: List[ScoredSignal]) -> None:
        """
        Tente d'envoyer des ordres via ExecutionEngine à partir
        des ScoredSignals.
        """
        has_onchain = self.execution_engine is not None
        has_paper = self.paper_execution_engine is not None

        if not has_onchain and not has_paper:
            # Aucun moteur d'exécution actif -> mode analyse only
            return

        for s in scored:
            try:
                if s.position is None or s.position.notional is None:
                    continue

                notional = float(s.position.notional)
                if notional <= 0:
                    continue

                # Mapping SignalSide -> OrderSide (simplifié)
                if s.raw.side == SignalSide.LONG:
                    side = OrderSide.BUY
                elif s.raw.side == SignalSide.SHORT:
                    side = OrderSide.SELL
                else:
                    # neutre: pas d'ordre
                    continue

                ctx = s.raw.context
                chain = self._normalize_chain(ctx.chain or "ethereum")
                base = ctx.base_token or ctx.symbol or "UNKNOWN"
                quote = ctx.quote_token or "USD"

                symbol = ctx.symbol or f"{base}-{quote}"

                # ============================
                #  Wallet routing (multi-wallet)
                # ============================

                wallet_id: Optional[str] = None
                twm = self._ensure_trade_wallet_manager()
                if twm is not None:
                    try:
                        wallet_id = twm.get_wallet_for_chain(
                            chain=chain, purpose="trading"
                        )
                    except Exception:
                        logger.debug(
                            "Erreur lors de la résolution du wallet pour chain=%s",
                            chain,
                            exc_info=True,
                        )

                # ============================
                #  RiskEngine (module 6)
                # ============================

                adjusted_notional = notional
                apply_risk = (
                    self.risk_engine is not None
                    and RiskDecision is not None
                    and OrderContext is not None
                )
                if apply_risk:
                    # equity approximative via WatchlistWalletManager (par chain)
                    balance_usd = 0.0
                    if (
                        self.watchlist_wallet_manager is not None
                        and hasattr(self.watchlist_wallet_manager, "get_equity")
                    ):
                        try:
                            eq = self.watchlist_wallet_manager.get_equity(chain)
                            balance_usd = float(eq)
                        except Exception:
                            logger.debug(
                                "Erreur lors de la récupération de l'equity pour %s (RiskEngine)",
                                chain,
                                exc_info=True,
                            )

                    # Si aucune equity, on ne bloque pas le trade, on log juste
                    if balance_usd > 0:
                        try:
                            symbol_str = ctx.symbol or f"{base}-{quote}"
                            side_str = "buy" if side == OrderSide.BUY else "sell"

                            risk_ctx = OrderContext(
                                wallet_id=wallet_id or "default",
                                symbol=symbol_str,
                                side=side_str,
                                size=adjusted_notional,
                                notional_usd=adjusted_notional,
                                balance_usd=balance_usd,
                                open_positions=0,
                                daily_pnl_pct=0.0,
                                global_daily_pnl_pct=0.0,
                                consecutive_losing_trades=0,
                            )

                            decision: RiskDecision = self.risk_engine.evaluate(  # type: ignore
                                risk_ctx
                            )

                            if decision.block:
                                logger.info(
                                    "RiskEngine: trade bloqué wallet=%s symbol=%s reason=%s",
                                    wallet_id or "default",
                                    symbol_str,
                                    decision.reason,
                                )
                                continue

                            if decision.size_multiplier is not None:
                                adjusted_notional = notional * float(
                                    decision.size_multiplier
                                )

                        except Exception:
                            logger.debug(
                                "Erreur lors de l'application du RiskEngine",
                                exc_info=True,
                            )

                # ============================
                #  Execution (on-chain + paper)
                # ============================

                # Execution on-chain si autorisé
                if has_onchain and self._is_godmode():
                    try:
                        self._maybe_execute_onchain(
                            chain=chain,
                            symbol=symbol,
                            side=side,
                            notional_usd=adjusted_notional,
                            ctx=ctx,
                            wallet_id=wallet_id,
                        )
                    except Exception:
                        logger.debug(
                            "Erreur lors de l'appel à ExecutionEngine on-chain",
                            exc_info=True,
                        )

                # Execution PAPER dans tous les cas (pour backtest / stats)
                if has_paper:
                    try:
                        self._paper_execute_order(
                            chain=chain,
                            symbol=symbol,
                            side=side,
                            notional_usd=adjusted_notional,
                            ctx=ctx,
                        )
                    except Exception:
                        logger.debug(
                            "Erreur lors de l'exécution PAPER",
                            exc_info=True,
                        )

            except Exception as e:
                logger.debug(
                    "Erreur lors du traitement d'un ScoredSignal: %s",
                    e,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Watchlist / Wallet helpers
    # ------------------------------------------------------------------

    def _ensure_watchlist_wallet_manager(self) -> Optional[WatchlistWalletManager]:
        """
        Vérifie que le WatchlistWalletManager est présent.
        """
        if self.watchlist_wallet_manager is None:
            logger.debug("WatchlistWalletManager non configuré")
            return None

        return self.watchlist_wallet_manager

    def _ensure_trade_wallet_manager(self) -> Optional[TradeWalletManager]:
        """
        Vérifie que le TradeWalletManager (multi-wallet) est présent.
        """
        if self.trade_wallet_manager is None:
            logger.debug("TradeWalletManager non configuré")
            return None

        return self.trade_wallet_manager

    def _get_wallet_for_chain(
        self,
        chain: str,
        purpose: str = "trading",
    ) -> Optional[str]:
        """
        Détermine quel wallet utiliser pour une chain donnée.
        """
        twm = self._ensure_trade_wallet_manager()
        if twm is None:
            return None

        try:
            wallet_id = twm.get_wallet_for_chain(chain=chain, purpose=purpose)
            return wallet_id
        except Exception:
            logger.debug(
                "Erreur lors de la récupération du wallet pour chain=%s purpose=%s",
                chain,
                purpose,
                exc_info=True,
            )
            return None

    # ------------------------------------------------------------------
    # Mode "GODMODE" / multi-wallet integration (hook futur)
    # ------------------------------------------------------------------

    def _maybe_route_wallet_and_execute(
        self,
        chain: str,
        symbol: str,
        side: OrderSide,
        notional_usd: float,
        ctx: Optional[SignalContext] = None,
    ) -> None:
        """
        Hook potentiel pour router explicitement par wallet.
        Actuellement, la logique principale est dans _maybe_execute_signals.
        """
        wallet_id = self._get_wallet_for_chain(chain=chain, purpose="trading")
        self._maybe_execute_onchain(
            chain=chain,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
            ctx=ctx,
            wallet_id=wallet_id,
        )

    # ------------------------------------------------------------------
    # Hooks / Extensibilité
    # ------------------------------------------------------------------

    def on_start(self) -> None:
        """
        Hook optionnel appelé au démarrage de l'AgentEngine.
        """
        logger.info("AgentEngine.on_start: hook appelé (implémentation par défaut)")

    def on_stop(self) -> None:
        """
        Hook optionnel appelé à l'arrêt de l'AgentEngine.
        """
        logger.info("AgentEngine.on_stop: hook appelé (implémentation par défaut)")

    def on_error(self, error: Exception) -> None:
        """
        Hook optionnel appelé lorsqu'une erreur survient dans run_forever.
        """
        logger.debug("AgentEngine.on_error: %s", error, exc_info=True)

    # ------------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_chain(chain_raw: Any) -> str:
        """
        Normalise le nom de la chain pour tout l'AgentEngine.
        """
        if not chain_raw:
            return "ethereum"

        c = str(chain_raw).strip().lower()

        # Ethereum
        if c in ("eth", "ethereum", "eth-mainnet", "mainnet"):
            return "ethereum"

        # BSC
        if c in ("bsc", "binance-smart-chain", "bnb", "bsc-mainnet"):
            return "bsc"

        # Solana
        if c in ("sol", "solana", "sol-mainnet-beta", "sol-mainnet"):
            return "solana"

        # Autres chains (arbitrum, base, etc.) : on renvoie juste en minuscule
        return c

    def _record_error(self, msg: str) -> None:
        self.state.last_error = msg
        self.state.consecutive_failures += 1

        if self.alert_engine is not None:
            try:
                if self.state.consecutive_failures in (1, 3, 5, 10):
                    self.alert_engine.warning(
                        "AgentEngine: problème API events",
                        source="agent_engine",
                        error=msg,
                        failures=self.state.consecutive_failures,
                    )
            except Exception:
                logger.debug(
                    "Impossible d'envoyer l'alerte via alert_engine", exc_info=True
                )
