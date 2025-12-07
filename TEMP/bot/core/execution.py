from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Sequence

from bot.core.runtime import (
    ExecutionEngineIface,
    ExecutionMode,
    RiskEngineIface,
    WalletManagerIface,
)
from bot.core.signals import TradeSignal

# -------------------------------------------------------------------
# Chemins runtime (communs au dashboard /godmode)
# -------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parents[2]
GODMODE_DIR = BASE_DIR / "data" / "godmode"

TRADES_PATH = GODMODE_DIR / "trades.jsonl"
EXECUTION_RUNTIME_PATH = GODMODE_DIR / "execution_runtime.json"

log = logging.getLogger(__name__)


class TradeStore:
    """
    Append-only JSONL pour les trades runtime.

    Chaque trade est un dict sérialisé sur une ligne dans trades.jsonl.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, trade: Dict[str, Any]) -> None:
        def _default(o: Any) -> Any:
            if isinstance(o, Decimal):
                return str(o)
            return str(o)

        line = json.dumps(trade, ensure_ascii=False, default=_default)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class RiskAwareExecutionEngine(ExecutionEngineIface):
    """
    ExecutionEngine M7-lite :

    - ne supporte que PAPER_ONCHAIN (LIVE bloqué par le runtime M1),
    - transforme les TradeSignal en trades papier,
    - loggue dans data/godmode/trades.jsonl,
    - maintient un snapshot light dans execution_runtime.json.
    """

    def __init__(
        self,
        risk_engine: RiskEngineIface,
        wallet_manager: WalletManagerIface,
    ) -> None:
        self._risk_engine = risk_engine
        self._wallet_manager = wallet_manager
        self._store = TradeStore(TRADES_PATH)
        self._logger = logging.getLogger("RiskAwareExecutionEngine")

        self._logger.info(
            "RiskAwareExecutionEngine INIT — base_dir=%s, trades_path=%s",
            str(BASE_DIR),
            str(TRADES_PATH),
        )

        # snapshot initial pour le dashboard
        self._persist_runtime_snapshot(initial=True)

    # ---------------------------------------------------------------
    # Interface ExecutionEngineIface
    # ---------------------------------------------------------------
    def process_signals(
        self,
        signals: Sequence[TradeSignal],
        mode: ExecutionMode,
    ) -> None:
        if not signals:
            return

        if mode is not ExecutionMode.PAPER_ONCHAIN:
            self._logger.warning(
                "ExecutionMode %s non supporté, aucun trade exécuté.",
                getattr(mode, "value", mode),
            )
            return

        for sig in signals:
            try:
                self._execute_paper_trade(sig)
            except Exception:
                self._logger.exception(
                    "Erreur lors de l'exécution papier pour le signal %r", sig
                )

    def on_tick(self) -> None:
        # Pour l’instant : on rafraîchit juste le snapshot risk/execution.
        self._persist_runtime_snapshot()

    # ---------------------------------------------------------------
    # Internes
    # ---------------------------------------------------------------
    def _execute_paper_trade(self, sig: TradeSignal) -> None:
        """
        Simulation ultra-simple :

        - prix fictif = 1 USD,
        - qty = notional_usd,
        - pas de PnL ni de gestion de position (M8/M10).
        """
        notional = Decimal(str(getattr(sig, "notional_usd", 0.0) or 0.0))
        price = Decimal("1")
        qty = notional if price == 0 else notional / price

        meta = dict(getattr(sig, "meta", {}) or {})
        meta.setdefault("strategy", getattr(sig, "strategy_id", "unknown"))
        meta.setdefault("strategy_tag", getattr(sig, "strategy_id", "unknown"))
        meta.setdefault("wallet_id", getattr(sig, "wallet_id", "unknown"))

        side_val = getattr(sig.side, "value", str(sig.side))

        trade: Dict[str, Any] = {
            "id": getattr(sig, "id", f"trade-{datetime.utcnow().timestamp()}"),
            "chain": meta.get("chain") or meta.get("network") or "SOL",
            "symbol": getattr(sig, "symbol", "UNKNOWN/USDC"),
            "side": str(side_val).lower(),
            "qty": f"{qty:.8f}",
            "price": f"{price:.8f}",
            "notional": f"{notional:.2f}",
            "notional_usd": float(notional),
            "fee": "0",
            "status": "executed",
            "created_at": datetime.utcnow().isoformat(),
            "meta": meta,
        }

        self._store.append(trade)

        self._logger.info(
            "[PAPER] Trade exécuté — wallet=%s %s %s notional=%.2f USD",
            meta.get("wallet_id"),
            side_val,
            trade["symbol"],
            float(notional),
        )

        # Pour l’instant on ne propage PAS de PnL vers les wallets :
        # le PnL reste 0 tant qu’on n’a pas M8/M10.

    # ---------------------------------------------------------------
    # Snapshot risk/execution pour le dashboard
    # ---------------------------------------------------------------
    def _build_runtime_snapshot(self) -> Dict[str, Any]:
        """
        Essaie de tirer quelques infos du RiskEngine si possible,
        sinon fournit un snapshot par défaut.
        """
        snap: Dict[str, Any] = {
            "risk_enabled": True,
            "daily_drawdown_pct": 0.0,
            "soft_stop_active": False,
            "hard_stop_active": False,
        }

        risk = self._risk_engine

        # Si le RiskEngine expose un snapshot structuré, on le merge.
        try:
            raw = None
            if hasattr(risk, "runtime_snapshot"):
                fn = getattr(risk, "runtime_snapshot")
                if callable(fn):
                    raw = fn()
            elif hasattr(risk, "debug_snapshot"):
                fn = getattr(risk, "debug_snapshot")
                if callable(fn):
                    raw = fn()

            if isinstance(raw, dict):
                for k in ("risk_enabled", "daily_drawdown_pct",
                          "soft_stop_active", "hard_stop_active", "kill_switch"):
                    if k in raw:
                        snap[k] = raw[k]
        except Exception:
            self._logger.exception(
                "Erreur lors de la récupération du snapshot RiskEngine, "
                "utilisation des valeurs par défaut."
            )

        # kill_switch : on essaie de récupérer quelque chose de cohérent
        if "kill_switch" not in snap:
            ks = getattr(risk, "kill_switch", None)
            if isinstance(ks, dict):
                snap["kill_switch"] = ks
            elif ks is not None:
                snap["kill_switch"] = {
                    "enabled": getattr(ks, "enabled", None),
                    "tripped": getattr(ks, "tripped", None),
                    "reason": getattr(ks, "reason", None),
                }
            else:
                snap["kill_switch"] = {
                    "enabled": True,
                    "tripped": False,
                    "reason": None,
                }

        snap["updated_at"] = datetime.utcnow().isoformat()
        return snap

    def _persist_runtime_snapshot(self, initial: bool = False) -> None:
        try:
            GODMODE_DIR.mkdir(parents=True, exist_ok=True)
            snapshot = self._build_runtime_snapshot()
            EXECUTION_RUNTIME_PATH.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            if initial:
                self._logger.info(
                    "ExecutionRuntime snapshot initial écrit — path=%s",
                    str(EXECUTION_RUNTIME_PATH),
                )
        except Exception:
            self._logger.exception(
                "Erreur lors de la persistance de execution_runtime.json"
            )
