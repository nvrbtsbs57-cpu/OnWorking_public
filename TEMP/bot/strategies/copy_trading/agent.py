# bot/strategies/copy_trading/agent.py

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Protocol, Sequence

from bot.core.signals import TradeSignal, SignalKind, SignalSide


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modèles de config & événements copy trading
# ---------------------------------------------------------------------------


@dataclass
class CopyMasterConfig:
    """
    Config pour un master de copy trading.

    - master_id: identifiant logique du master (peut être une adresse, un nom, etc.)
    - target_wallet_id: wallet local sur lequel reproduire les trades (ex: "copy_sol")
    - size_mode:
        * "fixed_notional"   : on prend toujours un notional fixe.
        * "multiplier"       : notional = base_notional * size_multiplier.
    - fixed_notional_usd: montant fixe à copier (si size_mode == "fixed_notional").
    - base_notional_usd: notional de référence (si size_mode == "multiplier").
    - size_multiplier: facteur pour le mode "multiplier".
    - max_notional_usd: cap hard sur le notional envoyé au RiskEngine.
    """

    master_id: str
    target_wallet_id: str
    size_mode: str = "fixed_notional"
    fixed_notional_usd: Decimal = Decimal("0")
    base_notional_usd: Decimal = Decimal("0")
    size_multiplier: Decimal = Decimal("1.0")
    max_notional_usd: Decimal = Decimal("0")


@dataclass
class CopyTradeEvent:
    """
    Évènement brut de copy trading (ce qui vient de la source externe).

    - master_id: identifiant du master qui a émis le trade
    - symbol: pair (ex: "SOL/USDC")
    - side: BUY / SELL
    - notional_usd: taille du trade du master (en USD)
    - meta: blob libre (tx_hash, exchange, leverage, etc.)
    """

    master_id: str
    symbol: str
    side: SignalSide
    notional_usd: Decimal
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Interface de feed copy trading (M6-full)
# ---------------------------------------------------------------------------


class CopyTradingFeed(Protocol):
    """
    Interface pour un feed copy trading.

    Impl M6-full typique :
      - se connecte à un indexer, webhook, websocket, API CEX, etc.
      - normalise les trades masters en CopyTradeEvent.
      - renvoie les évènements nouveaux à chaque tick.
    """

    def poll_new_events(self) -> Sequence[CopyTradeEvent]:
        ...


class StubRandomCopyFeed:
    """
    Stub de feed copy trading :
      - génère aléatoirement des CopyTradeEvent pour tester le pipeline M6-lite.
    """

    def __init__(
        self,
        master_ids: Sequence[str],
        max_events_per_tick: int = 1,
        seed: Optional[int] = None,
    ) -> None:
        self._rng = random.Random(seed)
        self._master_ids = list(master_ids)
        self._max_events = max(int(max_events_per_tick), 0)

    def poll_new_events(self) -> Sequence[CopyTradeEvent]:
        if not self._master_ids or self._max_events <= 0:
            return []

        n = self._rng.randint(0, self._max_events)
        if n <= 0:
            return []

        events: List[CopyTradeEvent] = []
        for _ in range(n):
            master_id = self._rng.choice(self._master_ids)
            symbol = "SOL/USDC"
            side = SignalSide.BUY if self._rng.random() < 0.5 else SignalSide.SELL
            notional = Decimal(str(round(self._rng.uniform(20, 150), 2)))
            meta = {
                "source": "stub_random_copy_feed",
            }
            events.append(
                CopyTradeEvent(
                    master_id=master_id,
                    symbol=symbol,
                    side=side,
                    notional_usd=notional,
                    meta=meta,
                )
            )
        return events


# ---------------------------------------------------------------------------
# CopyTradingStrategyEngine (M6-lite)
# ---------------------------------------------------------------------------


class CopyTradingStrategyEngine:
    """
    Stratégie de copy trading "lite".

    Rôle :
      - récupérer des CopyTradeEvent depuis un feed (ou via feed_events()),
      - appliquer les règles de sizing par master,
      - émettre des TradeSignal pour le wallet local (ex: "copy_sol"),
      - laisser le RiskEngine/RiskAwareExecutionEngine faire le reste.

    Interface (comme StrategyEngineIface) :
      - next_signals() -> Sequence[TradeSignal]
      - on_tick()
    """

    def __init__(
        self,
        masters: Sequence[CopyMasterConfig],
        strategy_id: str = "copy_trading",
        feed: Optional[CopyTradingFeed] = None,
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self._logger = logger_ or logging.getLogger(__name__)
        self._masters_by_id: Dict[str, CopyMasterConfig] = {
            m.master_id: m for m in masters
        }
        self._strategy_id = strategy_id
        self._feed = feed

        # File d'évènements en attente (peut être alimentée via feed_events())
        self._pending_events: List[CopyTradeEvent] = []

        self._logger.info(
            "CopyTradingStrategyEngine initialisé avec %d masters: %s",
            len(self._masters_by_id),
            list(self._masters_by_id.keys()),
        )

    # ------------------------------------------------------------------
    # Alimentation en évènements
    # ------------------------------------------------------------------

    def set_feed(self, feed: CopyTradingFeed) -> None:
        self._feed = feed

    def feed_events(self, events: Sequence[CopyTradeEvent]) -> None:
        """
        Alimente la stratégie avec des évènements bruts (tests, pipeline externe).
        """
        self._pending_events.extend(events)
        self._logger.debug(
            "feed_events() — ajout de %d évènements, total=%d",
            len(events),
            len(self._pending_events),
        )

    def _pull_from_feed(self) -> None:
        """
        Si un feed est configuré, on récupère les nouveaux évènements à ce tick.
        """
        if self._feed is None:
            return

        try:
            new_events = list(self._feed.poll_new_events())
        except Exception:
            self._logger.exception(
                "Erreur lors de feed.poll_new_events(), feed désactivé pour ce tick."
            )
            return

        if not new_events:
            return

        self._pending_events.extend(new_events)
        self._logger.debug(
            "_pull_from_feed() — feed a ajouté %d évènements, total=%d",
            len(new_events),
            len(self._pending_events),
        )

    # ------------------------------------------------------------------
    # Conversion évènement -> TradeSignal
    # ------------------------------------------------------------------

    def _event_to_signal(self, ev: CopyTradeEvent) -> Optional[TradeSignal]:
        master_cfg = self._masters_by_id.get(ev.master_id)
        if master_cfg is None:
            self._logger.debug(
                "CopyTradeEvent ignoré (master non configuré): %s", ev.master_id
            )
            return None

        # sizing
        if master_cfg.size_mode == "fixed_notional":
            notional = master_cfg.fixed_notional_usd
        elif master_cfg.size_mode == "multiplier":
            notional = master_cfg.base_notional_usd * master_cfg.size_multiplier
        else:
            self._logger.warning(
                "size_mode inconnu pour master %s: %s, évènement ignoré.",
                master_cfg.master_id,
                master_cfg.size_mode,
            )
            return None

        if master_cfg.max_notional_usd > 0 and notional > master_cfg.max_notional_usd:
            notional = master_cfg.max_notional_usd

        if notional <= 0:
            self._logger.debug(
                "CopyTradeEvent ignoré (notional <= 0) pour master=%s symbol=%s",
                master_cfg.master_id,
                ev.symbol,
            )
            return None

        sig_id = f"copy:{master_cfg.master_id}:{ev.symbol}:{ev.side.value}"

        meta = dict(ev.meta)
        meta.setdefault("strategy", self._strategy_id)
        meta.setdefault("master_id", master_cfg.master_id)

        signal = TradeSignal(
            id=sig_id,
            strategy_id=self._strategy_id,
            wallet_id=master_cfg.target_wallet_id,
            symbol=ev.symbol,
            side=ev.side,
            notional_usd=float(notional),
            kind=SignalKind.ENTRY,
            meta=meta,
        )
        return signal

    # ------------------------------------------------------------------
    # API runtime (StrategyEngineIface)
    # ------------------------------------------------------------------

    def next_signals(self) -> Sequence[TradeSignal]:
        """
        Méthode appelée par BotRuntime à chaque tick.

        - Récupère les nouveaux évènements depuis le feed (si présent),
        - consomme la file d'évènements → génère des TradeSignal ENTRY.
        """
        # 1) Feed externe
        self._pull_from_feed()

        # 2) Consommation de la file
        if not self._pending_events:
            return []

        events = self._pending_events
        self._pending_events = []

        signals: List[TradeSignal] = []
        for ev in events:
            sig = self._event_to_signal(ev)
            if sig is None:
                continue
            signals.append(sig)

        if signals:
            self._logger.info(
                "CopyTradingStrategy — %d signaux générés à partir de %d évènements.",
                len(signals),
                len(events),
            )
        else:
            self._logger.info(
                "CopyTradingStrategy — aucun signal généré à partir de %d évènements.",
                len(events),
            )

        return signals

    def on_tick(self) -> None:
        """
        Hook appelé après next_signals() par le runtime.
        Pour M6-lite : no-op.
        """
        return None


# ---------------------------------------------------------------------------
# Builder depuis config.json (M6-lite)
# ---------------------------------------------------------------------------


def build_copy_trading_strategy_from_config(
    raw_cfg: Dict[str, Any],
    logger_: Optional[logging.Logger] = None,
) -> CopyTradingStrategyEngine:
    """
    Construit un CopyTradingStrategyEngine à partir de config.json.

    Config attendue (exemple) :

      "strategies": {
        "copy_trading": {
          "enabled": true,
          "strategy_id": "copy_trading",
          "masters": [
            {
              "master_id": "master_1",
              "target_wallet_id": "copy_sol",
              "size_mode": "fixed_notional",
              "fixed_notional_usd": 50,
              "max_notional_usd": 200
            }
          ],
          "feed": {
            "kind": "stub_random",
            "max_events_per_tick": 1,
            "seed": 123
          }
        }
      }

    Si la section est absente ou disabled, on renvoie un engine avec 0 masters
    et aucun feed → next_signals() retournera toujours [].
    """
    log = logger_ or logging.getLogger("CopyTradingStrategy")

    strategies = raw_cfg.get("strategies", {}) or {}
    cfg_raw = strategies.get("copy_trading", {}) or {}

    enabled = bool(cfg_raw.get("enabled", False))
    strategy_id = str(cfg_raw.get("strategy_id", "copy_trading"))

    masters_raw = cfg_raw.get("masters", []) or []
    masters: List[CopyMasterConfig] = []

    for m in masters_raw:
        try:
            master_id = str(m["master_id"])
            target_wallet_id = str(m.get("target_wallet_id", "copy_sol"))
        except KeyError as exc:
            log.warning(
                "Master copy trading invalide (clé manquante: %s): %r", exc, m
            )
            continue

        size_mode = str(m.get("size_mode", "fixed_notional")).lower()
        fixed_notional = Decimal(str(m.get("fixed_notional_usd", "0")))
        base_notional = Decimal(str(m.get("base_notional_usd", "0")))
        size_multiplier = Decimal(str(m.get("size_multiplier", "1.0")))
        max_notional = Decimal(str(m.get("max_notional_usd", "0")))

        masters.append(
            CopyMasterConfig(
                master_id=master_id,
                target_wallet_id=target_wallet_id,
                size_mode=size_mode,
                fixed_notional_usd=fixed_notional,
                base_notional_usd=base_notional,
                size_multiplier=size_multiplier,
                max_notional_usd=max_notional,
            )
        )

    if not enabled:
        log.info(
            "CopyTradingStrategy désactivée dans config.json (enabled=false)."
        )
        return CopyTradingStrategyEngine(
            masters=[],
            strategy_id=strategy_id,
            feed=None,
            logger_=log,
        )

    # Feed stub (M6-lite)
    feed_cfg = cfg_raw.get("feed", {}) or {}
    feed_kind = str(feed_cfg.get("kind", "")).lower()

    feed: Optional[CopyTradingFeed] = None
    if feed_kind == "stub_random" and masters:
        max_events = int(feed_cfg.get("max_events_per_tick", 1))
        seed = feed_cfg.get("seed")
        feed = StubRandomCopyFeed(
            master_ids=[m.master_id for m in masters],
            max_events_per_tick=max_events,
            seed=seed,
        )

    engine = CopyTradingStrategyEngine(
        masters=masters,
        strategy_id=strategy_id,
        feed=feed,
        logger_=log,
    )
    return engine
