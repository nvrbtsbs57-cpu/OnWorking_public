# file: bot/trading/paper_trader.py
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from bot.core.logging import get_logger
from .models import AgentStatus, PnLStats, TradeSide, Trade
from .store import TradeStore, TradeStoreConfig, Trade as StoreTrade

logger = get_logger(__name__)
getcontext().prec = 50  # haute précision pour les montants


# ======================================================================
# TradeSignal interne au moteur paper
# ======================================================================


@dataclass
class TradeSignal:
    chain: str
    symbol: str
    side: TradeSide
    notional_usd: Decimal
    entry_price: Optional[Decimal] = None
    meta: Dict[str, Any] = field(default_factory=dict)


# ======================================================================
# Config PaperTrader
# ======================================================================


@dataclass
class PaperTraderConfig:
    path: str = "data/godmode/trades.jsonl"
    max_trades: int = 50_000
    default_chain: str = "ethereum"
    default_symbol: str = "ETH"

    @staticmethod
    def from_env() -> "PaperTraderConfig":
        path = os.getenv("PAPER_TRADES_PATH", "data/godmode/trades.jsonl")

        raw_max = os.getenv("PAPER_TRADES_MAX", "50000")
        try:
            max_trades = int(raw_max)
        except Exception:
            max_trades = 50_000

        default_chain = os.getenv("PAPER_DEFAULT_CHAIN", "ethereum")
        default_symbol = os.getenv("PAPER_DEFAULT_SYMBOL", "ETH")

        return PaperTraderConfig(
            path=path,
            max_trades=max_trades,
            default_chain=default_chain,
            default_symbol=default_symbol,
        )


# ======================================================================
# Moteur PaperTrader
# ======================================================================


class PaperTrader:
    """
    Moteur de paper trading :
    - journalise les trades dans un TradeStore
    - calcule un PnL agrégé via TradeStore.compute_pnl()
    - expose un AgentStatus lisible par le runtime / wallet manager / dashboard

    M11 : prise en charge des prix "réels" via:
      - prix fournis dans `prices[(chain, symbol)]` (PriceProvider)
      - ou `signal.entry_price`
      - fallback 1.0 seulement si aucun prix dispo, avec flag meta["price_missing"] = True
    """

    def __init__(self, config: PaperTraderConfig, store: Optional[TradeStore] = None) -> None:
        self.config = config

        if store is not None:
            # Injection d'un store externe (tests / override avancé)
            self.store = store
        else:
            path = Path(self.config.path)
            path.parent.mkdir(parents=True, exist_ok=True)

            store_cfg = TradeStoreConfig(
                base_dir=str(path.parent),
                trades_file=path.name,
                max_trades=self.config.max_trades,
            )
            self.store = TradeStore(store_cfg)

        self._last_pnl: Optional[PnLStats] = None
        self._agent_status = AgentStatus(
            is_running=True,
            last_heartbeat=datetime.utcnow(),
            meta={},
        )

        # Fee rate simulé (env PAPER_FEE_RATE, ex: "0.003" pour 0.3%)
        raw_fee = os.getenv("PAPER_FEE_RATE", "0")
        try:
            self._fee_rate = Decimal(raw_fee)
        except Exception:
            logger.warning(
                "PaperTrader: valeur PAPER_FEE_RATE invalide (%s), fallback à 0.",
                raw_fee,
            )
            self._fee_rate = Decimal("0")

        logger.info(
            "PaperTrader initialisé (path=%s, max_trades=%d, fee_rate=%s)",
            self.config.path,
            self.config.max_trades,
            self._fee_rate,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_chain(self, chain: Optional[str]) -> str:
        if not chain:
            return self.config.default_chain
        return str(chain).lower()

    def _normalize_symbol(self, symbol: Optional[str]) -> str:
        if not symbol:
            return self.config.default_symbol
        return str(symbol).upper()

    def _normalize_side(self, side: Any) -> TradeSide:
        """
        Normalise un "side" venant potentiellement de bot.core.signals (SignalSide),
        d'une string, ou déjà d'un TradeSide.
        """
        if isinstance(side, TradeSide):
            return side

        # SignalSide.BUY / SELL → value="buy"/"sell"
        val = getattr(side, "value", side)
        s = str(val).lower()

        if s in ("buy", "long"):
            return TradeSide.BUY
        if s in ("sell", "short"):
            return TradeSide.SELL

        raise ValueError(f"PaperTrader._normalize_side: side inconnu: {side!r}")

    def _ensure_price(
        self,
        *,
        signal: Any,
        chain: str,
        symbol: str,
        prices: Optional[Dict[Tuple[str, str], Any]] = None,
    ) -> Tuple[Decimal, bool, str]:
        """
        Garantit un Decimal pour le prix et retourne aussi:
          - un booléen `price_missing` (True si fallback)
          - une string `price_source` pour debug ("price_provider", "signal_entry_price", "fallback_1.0")

        Ordre de priorité:
          1) prices[(chain, symbol)] si fourni
          2) signal.entry_price
          3) fallback 1.0 avec flag price_missing=True
        """

        # 1) Prix fourni par PriceProvider (prices dict)
        if prices is not None:
            raw_mp = prices.get((chain, symbol))
            if raw_mp is not None:
                if isinstance(raw_mp, Decimal):
                    if raw_mp > 0:
                        return raw_mp, False, "price_provider"
                else:
                    try:
                        price = Decimal(str(raw_mp))
                        if price > 0:
                            return price, False, "price_provider"
                    except Exception:
                        logger.warning(
                            "PaperTrader: mark_price invalide %r pour %s/%s, ignoré",
                            raw_mp,
                            chain,
                            symbol,
                        )

        # 2) Prix fourni directement dans le signal (entry_price)
        raw_price = getattr(signal, "entry_price", None)
        if raw_price is not None:
            if isinstance(raw_price, Decimal):
                if raw_price > 0:
                    return raw_price, False, "signal_entry_price"
            else:
                try:
                    price = Decimal(str(raw_price))
                    if price > 0:
                        return price, False, "signal_entry_price"
                except Exception:
                    logger.warning(
                        "PaperTrader: entry_price invalide %r pour %s/%s, ignoré",
                        raw_price,
                        chain,
                        symbol,
                    )

        # 3) Fallback 1.0 (price_missing=True)
        logger.warning(
            "PaperTrader: aucun prix disponible pour chain=%s symbol=%s "
            "(ni prices ni entry_price), fallback 1.0 (price_missing=True)",
            chain,
            symbol,
        )
        return Decimal("1.0"), True, "fallback_1.0"

    def _compute_simulated_pnl_and_fees(
        self,
        *,
        chain: str,
        symbol: str,
        side: TradeSide,
        qty: Decimal,
        entry_price: Decimal,
        notional_usd: Decimal,
        prices: Optional[Dict[Tuple[str, str], Any]] = None,
        price_missing: bool = False,
    ) -> Tuple[Decimal, Decimal]:
        """
        Calcule un PnL et des fees simulés pour CE trade uniquement.

        - Si un prix de marché est présent dans `prices[(chain, symbol)]`,
          on fait un mark-to-market simple.
        - Si `price_missing=True`, on renvoie PnL=0 et fees=0 (mode safe).
        - Sinon, PnL=0 si pas de prix marché exploitable.
        - Fees = notional * self._fee_rate quand price_missing=False.
        """

        quant = Decimal("0.00000001")

        # Mode "safe" si aucun prix exploitable
        if price_missing:
            pnl_sim = Decimal("0")
            fees_sim = Decimal("0")
            return pnl_sim.quantize(quant), fees_sim.quantize(quant)

        mark_price: Optional[Decimal] = None
        if prices is not None:
            raw_mp = prices.get((chain, symbol))
            if raw_mp is not None:
                if isinstance(raw_mp, Decimal):
                    mark_price = raw_mp
                else:
                    try:
                        mark_price = Decimal(str(raw_mp))
                    except Exception:
                        logger.warning(
                            "PaperTrader: mark_price invalide %r, ignoré pour le PnL simulé",
                            raw_mp,
                        )
                        mark_price = None

        pnl_sim = Decimal("0")
        if mark_price is not None and qty > 0:
            if side == TradeSide.BUY:
                pnl_sim = (mark_price - entry_price) * qty
            else:
                # SELL / SHORT logique
                pnl_sim = (entry_price - mark_price) * qty

        fees_sim = Decimal("0")
        if self._fee_rate > 0:
            fees_sim = notional_usd * self._fee_rate

        pnl_sim = pnl_sim.quantize(quant)
        fees_sim = fees_sim.quantize(quant)

        return pnl_sim, fees_sim

    # ------------------------------------------------------------------
    # Coeur : traitement d'un TradeSignal
    # ------------------------------------------------------------------

    def process_signal(
        self,
        signal: Any,
        prices: Optional[Dict[Tuple[str, str], Any]] = None,
    ):
        """
        Traite un TradeSignal :
        - crée un Trade logique
        - l’adapte au modèle du TradeStore
        - met à jour le PnL global
        - met à jour l’AgentStatus (utilisé par le runtime / wallet manager)

        NB: `signal` peut être le TradeSignal interne OU un bot.core.signals.TradeSignal
            (ou encore le Signal memecoin_farming).
        """
        chain = self._normalize_chain(getattr(signal, "chain", None))
        symbol = self._normalize_symbol(getattr(signal, "symbol", None))

        raw_side = getattr(signal, "side", None)
        if raw_side is None:
            raise ValueError("PaperTrader.process_signal: signal.side manquant")

        side = self._normalize_side(raw_side)

        # Prix + infos de source (PriceProvider / signal / fallback)
        price, price_missing, price_source = self._ensure_price(
            signal=signal,
            chain=chain,
            symbol=symbol,
            prices=prices,
        )

        # Notional en Decimal (tolère float / int / Decimal)
        raw_notional = getattr(signal, "notional_usd", Decimal("0"))
        if isinstance(raw_notional, Decimal):
            notional = raw_notional
        else:
            try:
                notional = Decimal(str(raw_notional))
            except Exception:
                logger.warning(
                    "PaperTrader: notional_usd invalide %r, fallback 0",
                    raw_notional,
                )
                notional = Decimal("0")

        # Quantité (évite Decimal / float : ici tout est Decimal)
        if price <= 0 or notional <= 0:
            qty = Decimal("0")
        else:
            qty = (notional / price).quantize(Decimal("0.00000001"))

        # PnL/fees simulés pour ce trade (utile pour le dashboard plus tard)
        pnl_sim, fees_sim = self._compute_simulated_pnl_and_fees(
            chain=chain,
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=price,
            notional_usd=notional,
            prices=prices,
            price_missing=price_missing,
        )

        # Trade logique (modèle principal)
        base_meta = getattr(signal, "meta", {}) or {}
        meta: Dict[str, Any] = {
            **base_meta,
            "pnl_sim_usd": str(pnl_sim),
            "fees_sim_usd": str(fees_sim),
            "price_source": price_source,
            "entry_price_used": str(price),
        }
        if price_missing:
            meta["price_missing"] = True

        trade = Trade.new(
            chain=chain,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            notional=notional,
            fee=fees_sim,
            meta=meta,
        )

        # Adaptation vers le Trade du TradeStore
        store_trade = StoreTrade(
            id=trade.id,
            chain=trade.chain,
            symbol=trade.symbol,
            side=trade.side,
            qty=trade.qty,
            price=trade.price,
            notional=trade.notional,
            fee=trade.fee,
            status=trade.status.value,
            created_at=trade.created_at,
            meta=trade.meta,
        )

        # PnL global AVANT ce trade
        prev_total = self._last_pnl.total if self._last_pnl is not None else Decimal("0")

        # On journalise le trade
        self.store.append_trade(store_trade)

        # PnL global APRÈS ce trade
        pnl = self.store.compute_pnl()
        self._last_pnl = pnl

        # PnL de CE trade = delta du PnL total
        trade_pnl = pnl.total - prev_total

        # Mise à jour de l’état de l’agent
        now = datetime.utcnow()
        self._agent_status.last_heartbeat = now
        self._agent_status.last_trade = trade
        self._agent_status.pnl = pnl
        self._agent_status.meta["last_trade"] = trade.id
        self._agent_status.meta["last_trade_pnl_usd"] = str(trade_pnl)

        logger.info(
            (
                "PaperTrader: trade simulé id=%s chain=%s symbol=%s side=%s "
                "notional=%s pnl_trade_usd=%s pnl_sim_usd=%s fees_sim_usd=%s "
                "price_source=%s price_missing=%s"
            ),
            trade.id,
            trade.chain,
            trade.symbol,
            trade.side.value,
            trade.notional,
            trade_pnl,
            pnl_sim,
            fees_sim,
            price_source,
            price_missing,
        )

        return trade, pnl, self._agent_status

    # ------------------------------------------------------------------
    # API simple
    # ------------------------------------------------------------------

    def execute_signal(
        self,
        signal: Any,
        prices: Optional[Dict[Tuple[str, str], Any]] = None,
    ):
        trade, _pnl, _status = self.process_signal(signal, prices=prices)
        return trade

    def get_pnl(self) -> Optional[PnLStats]:
        return self._last_pnl

    def get_recent_trades(self, limit: int = 50):
        return self.store.get_recent_trades(limit=limit)

    def get_agent_status(self) -> AgentStatus:
        self._agent_status.last_heartbeat = datetime.utcnow()
        return self._agent_status


# Alias rétro-compat
PaperTradingEngine = PaperTrader

