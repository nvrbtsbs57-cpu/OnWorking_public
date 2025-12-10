# bot/trading/store.py

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bot.core.logging import get_logger
from bot.trading.models import TradeSide, PnLStats  # Enum buy/sell + PnLStats

logger = get_logger(__name__)
getcontext().prec = 50  # haute précision pour les prix / quantités


# ======================================================================
# Modèle de Trade pour le Store (paper trades sérialisés)
# ======================================================================


@dataclass
class Trade:
    id: str
    chain: str
    symbol: str
    side: TradeSide
    qty: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal = Decimal("0")
    status: str = "executed"
    created_at: datetime = datetime.utcnow()
    meta: Dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.meta is None:
            self.meta = {}

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["qty"] = str(self.qty)
        d["price"] = str(self.price)
        d["notional"] = str(self.notional)
        d["fee"] = str(self.fee)
        d["created_at"] = self.created_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Trade:
        side_raw = data.get("side") or data.get("direction") or "buy"
        side = TradeSide(side_raw.lower())

        def _dec(x: Any, default: str = "0") -> Decimal:
            if x is None:
                return Decimal(default)
            return Decimal(str(x))

        created_raw = data.get("created_at") or data.get("ts") or data.get("time")
        if isinstance(created_raw, datetime):
            created_at = created_raw
        else:
            try:
                created_at = datetime.fromisoformat(str(created_raw))
            except Exception:
                created_at = datetime.utcnow()

        return cls(
            id=str(data.get("id") or data.get("trade_id") or uuid.uuid4().hex),
            chain=str(data.get("chain") or "unknown"),
            symbol=str(data.get("symbol") or data.get("market") or "UNKNOWN"),
            side=side,
            qty=_dec(data.get("qty")),
            price=_dec(data.get("price")),
            notional=_dec(data.get("notional")),
            fee=_dec(data.get("fee")),
            status=str(data.get("status") or "executed"),
            created_at=created_at,
            meta=dict(data.get("meta") or {}),
        )


# ======================================================================
# Positions + PnL
# ======================================================================


@dataclass
class Position:
    chain: str
    symbol: str
    total_qty: Decimal
    avg_entry_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chain": self.chain,
            "symbol": self.symbol,
            "total_qty": str(self.total_qty),
            "avg_entry_price": str(self.avg_entry_price),
            "unrealized_pnl": str(self.unrealized_pnl),
            "realized_pnl": str(self.realized_pnl),
        }


@dataclass
class PnLSummary:
    total: Decimal
    realized: Decimal
    unrealized: Decimal
    nb_trades: int
    win_rate: float
    nb_winners: int
    nb_losers: int
    currency: str = "USD"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": str(self.total),
            "realized": str(self.realized),
            "unrealized": str(self.unrealized),
            "nb_trades": self.nb_trades,
            "win_rate": float(self.win_rate),
            "nb_winners": self.nb_winners,
            "nb_losers": self.nb_losers,
            "currency": self.currency,
        }


# ======================================================================
# Config + Store
# ======================================================================


@dataclass
class TradeStoreConfig:
    base_dir: str = "data/godmode"
    trades_file: str = "trades.jsonl"
    max_trades: int = 50_000

    @property
    def path(self) -> Path:
        return Path(self.base_dir) / self.trades_file


class TradeStore:
    def __init__(self, config: TradeStoreConfig) -> None:
        self.config = config
        path = self.config.path
        os.makedirs(path.parent, exist_ok=True)
        if not path.exists():
            path.touch()
        logger.info(
            "TradeStore initialisé (path=%s, max_trades=%d)",
            path,
            self.config.max_trades,
        )

    # ------------------------------------------------------------------
    # Ecriture
    # ------------------------------------------------------------------

    def append_trade(self, trade: Trade) -> None:
        path = self.config.path
        line = json.dumps(trade.to_dict(), ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # Lecture
    # ------------------------------------------------------------------

    def get_trades(self) -> List[Trade]:
        path = self.config.path
        trades: List[Trade] = []
        if not path.exists():
            return trades

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    trades.append(Trade.from_dict(data))
                except Exception:
                    logger.exception(
                        "Erreur de parsing d'une ligne JSONL trade",
                        extra={"line": line},
                    )
                    continue

        if len(trades) > self.config.max_trades:
            trades = trades[-self.config.max_trades :]
        return trades

    def get_recent_trades(self, limit: int = 50) -> List[Trade]:
        all_trades = self.get_trades()
        if limit <= 0:
            return []
        if len(all_trades) <= limit:
            return all_trades
        return all_trades[-limit:]

    # ------------------------------------------------------------------
    # Positions + PnL
    # ------------------------------------------------------------------

    def compute_positions_and_pnl(
        self,
        prices: Optional[Dict[Tuple[str, str], Decimal]] = None,
    ) -> Tuple[Dict[Tuple[str, str], Position], PnLSummary]:
        trades = self.get_trades()
        positions: Dict[Tuple[str, str], Position] = {}

        realized_total = Decimal("0")
        winning_trades = 0
        losing_trades = 0

        # 1er passage : calcul des positions agrégées
        for t in trades:
            key = (t.chain, t.symbol)
            pos = positions.get(key)
            if pos is None:
                pos = Position(
                    chain=t.chain,
                    symbol=t.symbol,
                    total_qty=Decimal("0"),
                    avg_entry_price=Decimal("0"),
                    unrealized_pnl=Decimal("0"),
                    realized_pnl=Decimal("0"),
                )

            new_total_qty = pos.total_qty + t.qty
            if new_total_qty > 0:
                if pos.total_qty == 0:
                    new_avg_price = t.price
                else:
                    new_avg_price = (
                        (pos.avg_entry_price * pos.total_qty) + (t.price * t.qty)
                    ) / new_total_qty
            else:
                new_avg_price = pos.avg_entry_price

            pos.total_qty = new_total_qty
            pos.avg_entry_price = new_avg_price
            positions[key] = pos

        # 2e passage : realized PnL (simplifié, non FIFO précis)
        positions_for_pnl: Dict[Tuple[str, str], Position] = {
            k: Position(
                chain=v.chain,
                symbol=v.symbol,
                total_qty=v.total_qty,
                avg_entry_price=v.avg_entry_price,
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("0"),
            )
            for k, v in positions.items()
        }

        for t in trades:
            key = (t.chain, t.symbol)
            pos = positions_for_pnl[key]
            if t.side == TradeSide.SELL and pos.total_qty > 0:
                pnl_for_trade = (t.price - pos.avg_entry_price) * t.qty
                pos.realized_pnl += pnl_for_trade
                realized_total += pnl_for_trade

                if pnl_for_trade > 0:
                    winning_trades += 1
                elif pnl_for_trade < 0:
                    losing_trades += 1

                pos.total_qty -= t.qty
                if pos.total_qty < 0:
                    pos.total_qty = Decimal("0")
                positions_for_pnl[key] = pos

        # Positions ouvertes + unrealized PnL
        open_positions: Dict[Tuple[str, str], Position] = {}
        for key, pos in positions_for_pnl.items():
            if pos.total_qty > 0:
                if prices is not None:
                    mark_price = prices.get(key)
                    if mark_price is not None:
                        pos.unrealized_pnl = (mark_price - pos.avg_entry_price) * pos.total_qty
                open_positions[key] = pos

        unrealized_total = sum((p.unrealized_pnl for p in open_positions.values()), Decimal("0"))
        total_pnl = realized_total + unrealized_total

        nb_trades = len(trades)
        if winning_trades + losing_trades > 0:
            win_rate = winning_trades / (winning_trades + losing_trades)
        else:
            win_rate = 0.0

        summary = PnLSummary(
            total=total_pnl,
            realized=realized_total,
            unrealized=unrealized_total,
            nb_trades=nb_trades,
            win_rate=win_rate,
            nb_winners=winning_trades,
            nb_losers=losing_trades,
        )

        return open_positions, summary

    def compute_pnl(self) -> PnLStats:
        """Wrapper utilisé par PaperTrader: retourne un PnLStats agrégé."""
        from datetime import datetime as _dt

        _positions, summary = self.compute_positions_and_pnl()
        return PnLStats(
            currency=summary.currency,
            realized=summary.realized,
            unrealized=summary.unrealized,
            total=summary.total,
            win_rate=summary.win_rate,
            nb_trades=summary.nb_trades,
            nb_winners=summary.nb_winners,
            nb_losers=summary.nb_losers,
            updated_at=_dt.utcnow(),
        )

    # ------------------------------------------------------------------
    # Reset des trades (utilisé par scripts/reset_trades.py)
    # ------------------------------------------------------------------

    def reset_trades(
        self,
        wallet_id: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> int:
        """
        Supprime des trades du fichier JSONL en fonction de filtres simples.

        - Si wallet_id et symbol sont None => reset GLOBAL (tous les trades).
        - Si wallet_id est fourni => on supprime les trades dont meta.wallet_id / meta.wallet /
          meta.wallet_name / meta.logical_wallet_id correspondent.
        - Si symbol est fourni => on supprime les trades pour ce symbol (ex: 'SOL/USDC').
        - Si wallet_id et symbol sont fournis => on supprime uniquement les trades qui matchent les deux.

        Retourne : nombre estimé de trades supprimés.
        """
        path = self.config.path
        if not path.exists():
            return 0

        # Reset global : on compte les lignes non vides puis on tronque le fichier
        if wallet_id is None and symbol is None:
            removed = 0
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        removed += 1

            # Troncature du fichier
            with open(path, "w", encoding="utf-8"):
                pass

            logger.warning(
                "TradeStore.reset_trades: GLOBAL reset, removed_trades=%d",
                removed,
            )
            return removed

        removed = 0
        kept_lines: List[str] = []

        def _match(data: Dict[str, Any]) -> bool:
            # Filtre wallet
            if wallet_id is not None:
                meta = data.get("meta") or {}
                candidates = [
                    meta.get("wallet_id"),
                    meta.get("wallet"),
                    meta.get("wallet_name"),
                    meta.get("logical_wallet_id"),
                ]
                ok_wallet = False
                for c in candidates:
                    if c is None:
                        continue
                    if str(c) == str(wallet_id):
                        ok_wallet = True
                        break
                if not ok_wallet:
                    return False

            # Filtre symbol
            if symbol is not None:
                sym_raw = data.get("symbol") or data.get("market")
                if str(sym_raw) != str(symbol):
                    return False

            return True

        # Lecture + filtrage
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except Exception:
                    # Ligne illisible => on la garde pour ne pas casser le fichier
                    kept_lines.append(raw)
                    continue

                if _match(data):
                    removed += 1
                    # on ne garde pas cette ligne
                    continue

                # On ré-écrit la ligne proprement (normalisée)
                kept_lines.append(json.dumps(data, ensure_ascii=False))

        # Ré-écriture du fichier avec uniquement les trades gardés
        with open(path, "w", encoding="utf-8") as f:
            for l in kept_lines:
                f.write(l + "\n")

        logger.warning(
            "TradeStore.reset_trades: removed_trades=%d wallet_id=%s symbol=%s",
            removed,
            wallet_id,
            symbol,
        )
        return removed
