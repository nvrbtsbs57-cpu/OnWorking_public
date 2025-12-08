# bot/core/trade_store.py

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any, Iterable, Mapping
import logging

from bot.core.signals import TradeSignal, SignalKind, SignalSide


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Modèle legacy (déjà utilisé) : RecordedTrade
# ---------------------------------------------------------------------------


@dataclass
class RecordedTrade:
    """
    Représentation minimale d'un trade exécuté (papier).

    - id            : identifiant logique (ex: signal.id)
    - wallet_id     : wallet logique (sniper_sol, base_main, etc.)
    - symbol        : marché (ex: SOL/USDC)
    - side          : "buy"/"sell"/"long"/"short" (string)
    - notional_usd  : taille en USD
    - pnl_usd       : PnL final du trade (0 tant qu'il est ouvert)
    - is_open       : True tant que la position n'est pas clôturée

    ⚠️ Ce modèle est conservé pour compat avec le code existant
       (M7-lite v1). La nouvelle logique de positions papier
       utilise OpenPosition / ClosedTrade ci-dessous.
    """
    id: str
    wallet_id: str
    symbol: str
    side: str
    notional_usd: Decimal
    pnl_usd: Decimal = Decimal("0")
    is_open: bool = True


# ---------------------------------------------------------------------------
# Nouveau modèle M7-lite++ : positions papier
# ---------------------------------------------------------------------------


@dataclass
class OpenPosition:
    """
    Position papier ouverte pour un couple (wallet_id, symbol).

    M7-lite++ : on part du principe 1 position par (wallet_id, symbol).
    """
    position_id: str             # ex: f"{wallet_id}:{symbol}"
    wallet_id: str
    symbol: str
    side: SignalSide
    size: Decimal                # taille en "unités" (ex: SOL, ETH)
    entry_price: Decimal         # prix d'entrée (USD)
    opened_at: datetime
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClosedTrade:
    """
    Trade papier clôturé (résultat de la fermeture d'une OpenPosition).
    """
    position_id: str
    wallet_id: str
    symbol: str
    side: SignalSide
    size: Decimal
    entry_price: Decimal
    close_price: Decimal
    pnl_usd: Decimal
    close_reason: SignalKind
    opened_at: datetime
    closed_at: datetime
    meta: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# TradeStore
# ---------------------------------------------------------------------------


class TradeStore:
    """
    M7-lite++ : stockage in-memory des trades et métriques associées.
    ...
    """

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        # Logger
        self._logger = logger or logging.getLogger("TradeStore")

        # Legacy trades (ancienne API)
        self._trades: Dict[str, RecordedTrade] = {}
        self._open_by_wallet: Dict[str, int] = {}

        # Losing streak globale (legacy + nouveau)
        self._consecutive_losing_trades: int = 0

        # Nouveau : positions papier
        # key = f"{wallet_id}:{symbol}"
        self._open_positions: Dict[str, OpenPosition] = {}
        self._closed_trades: List[ClosedTrade] = []


    # ======================================================================
    # Helpers internes
    # ======================================================================

    @staticmethod
    def _position_key(wallet_id: str, symbol: str) -> str:
        return f"{wallet_id}:{symbol}"

    def _compute_pnl(self, position: OpenPosition, close_price: Decimal) -> Decimal:
        """
        Calcule le PnL réalisé pour la fermeture de `position`.

        On essaie d'être robuste à différentes implémentations de SignalSide :
        - Enum avec .name ("LONG"/"SHORT", "BUY"/"SELL", etc.)
        """
        try:
            side_name = position.side.name.upper()
        except AttributeError:
            side_name = str(position.side).upper()

        if side_name in ("LONG", "BUY"):
            direction = Decimal("1")
        elif side_name in ("SHORT", "SELL"):
            direction = Decimal("-1")
        else:
            # Valeur inattendue : on logge et on assume "long"
            self._logger.warning("SignalSide inattendu pour calcul de PnL: %r", position.side)
            direction = Decimal("1")

        price_delta = close_price - position.entry_price
        return price_delta * position.size * direction

    # ======================================================================
    # API LEGACY : RecordedTrade (M7-lite v1)
    # ======================================================================

    def register_open_trade(
        self,
        trade_id: str,
        wallet_id: str,
        symbol: str,
        side: str,
        notional_usd: Decimal,
    ) -> None:
        """
        Enregistre un trade "ouvert" (entrée de position).

        API legacy, utilisée par le code existant.
        """
        if trade_id in self._trades and self._trades[trade_id].is_open:
            self._logger.warning(
                "register_open_trade() — trade_id=%s déjà ouvert, ignore.",
                trade_id,
            )
            return

        trade = RecordedTrade(
            id=trade_id,
            wallet_id=wallet_id,
            symbol=symbol,
            side=side,
            notional_usd=notional_usd,
        )
        self._trades[trade_id] = trade
        self._open_by_wallet[wallet_id] = self._open_by_wallet.get(wallet_id, 0) + 1

        self._logger.debug(
            "register_open_trade() — id=%s wallet=%s symbol=%s side=%s size=%.2f USD",
            trade_id,
            wallet_id,
            symbol,
            side,
            float(notional_usd),
        )

    def close_trade(self, trade_id: str, pnl_usd: Decimal) -> None:
        """
        Clôture un trade (legacy) et met à jour la losing streak globale.

        Règle :
          - pnl < 0  -> losing_streak += 1
          - pnl > 0  -> losing_streak = 0
          - pnl == 0 -> losing_streak inchangée
        """
        trade = self._trades.get(trade_id)
        if trade is None:
            self._logger.warning("close_trade() — trade_id=%s inconnu.", trade_id)
            return
        if not trade.is_open:
            self._logger.debug("close_trade() — trade_id=%s déjà fermé.", trade_id)
            return

        trade.is_open = False
        trade.pnl_usd = pnl_usd

        if trade.wallet_id in self._open_by_wallet:
            self._open_by_wallet[trade.wallet_id] = max(
                0, self._open_by_wallet[trade.wallet_id] - 1
            )

        if pnl_usd < 0:
            self._consecutive_losing_trades += 1
        elif pnl_usd > 0:
            self._consecutive_losing_trades = 0

        self._logger.info(
            "close_trade() — id=%s wallet=%s pnl=%.2f USD, losing_streak=%d",
            trade_id,
            trade.wallet_id,
            float(pnl_usd),
            self._consecutive_losing_trades,
        )

    # ======================================================================
    # API NOUVELLE : OpenPosition / ClosedTrade (M7-lite++)
    # ======================================================================

    def open_from_signal(
        self,
        signal: TradeSignal,
        executed_size: Decimal,
        entry_price: Decimal,
        *,
        now: Optional[datetime] = None,
    ) -> OpenPosition:
        """
        Ouvre ou remplace la position papier pour (wallet_id, symbol) à partir d'un TradeSignal ENTRY.

        - executed_size : taille réellement exécutée après passage dans le RiskEngine.
        - entry_price   : prix d'entrée utilisé pour le PnL papier.
        """
        if now is None:
            now = datetime.utcnow()

        key = self._position_key(signal.wallet_id, signal.symbol)

        position = OpenPosition(
            position_id=key,
            wallet_id=signal.wallet_id,
            symbol=signal.symbol,
            side=signal.side,
            size=executed_size,
            entry_price=entry_price,
            opened_at=now,
            meta=dict(getattr(signal, "meta", {}) or {}),
        )

        self._open_positions[key] = position
        # On maintient aussi le compteur par wallet pour rester cohérent avec l'API legacy
        self._open_by_wallet[signal.wallet_id] = self._open_by_wallet.get(signal.wallet_id, 0) + 1

        logger.debug(
            "open_from_signal() — wallet=%s symbol=%s side=%s size=%s entry_price=%s",
            position.wallet_id,
            position.symbol,
            getattr(position.side, "name", str(position.side)),
            str(position.size),
            str(position.entry_price),
        )

        return position

    # alias pratique si tu préfères un nom plus "métier"
    def register_entry(
        self,
        signal: TradeSignal,
        executed_size: Decimal,
        entry_price: Decimal,
        *,
        now: Optional[datetime] = None,
    ) -> OpenPosition:
        return self.open_from_signal(signal, executed_size, entry_price, now=now)

    def close_all_for(
        self,
        wallet_id: str,
        symbol: str,
        close_price: Decimal,
        reason: SignalKind,
        *,
        now: Optional[datetime] = None,
    ) -> List[ClosedTrade]:
        """
        Ferme la position papier (s'il y en a une) pour (wallet_id, symbol) au prix close_price.

        M7-lite++ : on considère une seule position par (wallet_id, symbol).

        Retourne une liste pour rester extensible (multi-positions plus tard).
        """
        if now is None:
            now = datetime.utcnow()

        key = self._position_key(wallet_id, symbol)
        position = self._open_positions.pop(key, None)

        if position is None:
            logger.debug(
                "close_all_for() — aucune position ouverte",
                extra={"wallet_id": wallet_id, "symbol": symbol, "reason": reason.name},
            )
            return []

        pnl_usd = self._compute_pnl(position, close_price)

        closed = ClosedTrade(
            position_id=position.position_id,
            wallet_id=position.wallet_id,
            symbol=position.symbol,
            side=position.side,
            size=position.size,
            entry_price=position.entry_price,
            close_price=close_price,
            pnl_usd=pnl_usd,
            close_reason=reason,
            opened_at=position.opened_at,
            closed_at=now,
            meta=position.meta,
        )

        self._closed_trades.append(closed)

        # MAJ losing streak globale (comme close_trade())
        if pnl_usd < 0:
            self._consecutive_losing_trades += 1
        elif pnl_usd > 0:
            self._consecutive_losing_trades = 0

        # MAJ compteur open_by_wallet pour cohérence avec legacy
        if wallet_id in self._open_by_wallet:
            self._open_by_wallet[wallet_id] = max(
                0, self._open_by_wallet[wallet_id] - 1
            )

        logger.info(
            "close_all_for() — wallet=%s symbol=%s reason=%s pnl=%s, losing_streak=%d",
            closed.wallet_id,
            closed.symbol,
            closed.close_reason.name,
            str(closed.pnl_usd),
            self._consecutive_losing_trades,
        )

        return [closed]

    def register_exit(
        self,
        wallet_id: str,
        symbol: str,
        close_price: Decimal,
        reason: SignalKind,
        *,
        now: Optional[datetime] = None,
    ) -> List[ClosedTrade]:
        """
        Alias métier pour close_all_for(), utilisable depuis l'ExecutionEngine.
        """
        return self.close_all_for(wallet_id, symbol, close_price, reason, now=now)

    # ======================================================================
    # Accès métriques / lecture
    # ======================================================================

    # ---- legacy (idem avant) ---------------------------------------------

    def get_open_positions(self, wallet_id: str) -> int:
        """
        Nombre de positions encore ouvertes pour un wallet (legacy).

        ⚠️ Utilise le compteur interne, mis à jour à la fois par :
           - register_open_trade / close_trade (legacy),
           - open_from_signal / close_all_for (nouveau).
        """
        return self._open_by_wallet.get(wallet_id, 0)

    def get_global_consecutive_losing_trades(self) -> int:
        """
        Série de trades perdants consécutifs (global).
        """
        return self._consecutive_losing_trades

    def get_open_trades(self, wallet_id: Optional[str] = None) -> List[RecordedTrade]:
        """
        Liste des trades encore ouverts (API legacy). Si wallet_id est spécifié,
        filtre sur ce wallet.
        """
        res: List[RecordedTrade] = []
        for t in self._trades.values():
            if not t.is_open:
                continue
            if wallet_id is not None and t.wallet_id != wallet_id:
                continue
            res.append(t)
        return res

    # ---- nouvelle API de lecture (positions papier) ----------------------

    @property
    def open_positions_map(self) -> Mapping[str, OpenPosition]:
        """
        Mapping clé (wallet:symbol) -> OpenPosition (lecture seule).
        """
        return self._open_positions

    @property
    def closed_trades(self) -> Iterable[ClosedTrade]:
        """
        Itérable de tous les ClosedTrade enregistrés.
        """
        return tuple(self._closed_trades)

    def get_open_position(self, wallet_id: str, symbol: str) -> Optional[OpenPosition]:
        """
        Récupère la position papier (wallet, symbol) si elle existe.
        """
        key = self._position_key(wallet_id, symbol)
        return self._open_positions.get(key)

    def get_open_positions_for_wallet(self, wallet_id: str) -> List[OpenPosition]:
        """
        Liste des positions papier ouvertes pour un wallet donné.
        """
        return [
            pos
            for pos in self._open_positions.values()
            if pos.wallet_id == wallet_id
        ]

    # ======================================================================
    # Utilitaires / debug
    # ======================================================================

    def reset(self) -> None:
        """
        Reset complet du store (utile pour les tests).
        """
        self._trades.clear()
        self._open_by_wallet.clear()
        self._consecutive_losing_trades = 0
        self._open_positions.clear()
        self._closed_trades.clear()

    def debug_snapshot(self) -> Dict[str, Any]:
        """
        Snapshot lisible des trades pour logs / debug.

        Conserve la structure existante et ajoute la vue "papier".
        """
        open_trades = [
            {
                "id": t.id,
                "wallet_id": t.wallet_id,
                "symbol": t.symbol,
                "side": t.side,
                "notional_usd": float(t.notional_usd),
            }
            for t in self._trades.values()
            if t.is_open
        ]

        paper_open_positions = [
            {
                "position_id": p.position_id,
                "wallet_id": p.wallet_id,
                "symbol": p.symbol,
                "side": getattr(p.side, "name", str(p.side)),
                "size": float(p.size),
                "entry_price": float(p.entry_price),
                "opened_at": p.opened_at.isoformat(),
            }
            for p in self._open_positions.values()
        ]

        paper_closed_trades = [
            {
                "position_id": c.position_id,
                "wallet_id": c.wallet_id,
                "symbol": c.symbol,
                "side": getattr(c.side, "name", str(c.side)),
                "size": float(c.size),
                "entry_price": float(c.entry_price),
                "close_price": float(c.close_price),
                "pnl_usd": float(c.pnl_usd),
                "reason": c.close_reason.name,
                "opened_at": c.opened_at.isoformat(),
                "closed_at": c.closed_at.isoformat(),
            }
            for c in self._closed_trades
        ]

        return {
            # legacy
            "open_positions_by_wallet": dict(self._open_by_wallet),
            "consecutive_losing_trades": self._consecutive_losing_trades,
            "open_trades": open_trades,
            # nouveau
            "paper_open_positions": paper_open_positions,
            "paper_closed_trades": paper_closed_trades,
        }
