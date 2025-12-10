from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from .engine import WalletFlowsEngine
from .factory import build_wallet_engine_from_config

# ------------------------------------------------------------
# Chemins runtime (communs au dashboard /godmode)
# ------------------------------------------------------------

# Racine du repo : BOT_GODMODE (bot/, data/, scripts/, ...)
BASE_DIR = Path(__file__).resolve().parents[2]

# Dossier GODMODE
GODMODE_DIR = BASE_DIR / "data" / "godmode"

# Fichier lu par l'API /godmode/wallets/runtime
RUNTIME_WALLETS_PATH = GODMODE_DIR / "wallets_runtime.json"

_SNAPSHOT_LOCK = threading.Lock()


class RuntimeWalletManager:
    """
    Wrap de WalletFlowsEngine pour le runtime GODMODE.

    - appelé par le runtime sur on_tick() et on_trade_closed()
    - écrit régulièrement data/godmode/wallets_runtime.json

    Format du fichier :

    {
      "updated_at": "...",
      "wallets": { "sniper_sol": {...}, ... },
      "wallets_count": 10,
      "equity_total_usd": 150.0,
      "pnl_day": {
        "total_realized_usd": 0.0,
        "total_fees_usd": 0.0
      }
    }
    """

    def __init__(
        self,
        engine: WalletFlowsEngine,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._engine = engine
        self._logger = logger or logging.getLogger(__name__)

        self._logger.info(
            "RuntimeWalletManager INIT — base_dir=%s, snapshot_path=%s",
            str(BASE_DIR),
            str(RUNTIME_WALLETS_PATH),
        )

        # Snapshot initial pour le dashboard
        self._persist_snapshot()

    # --------------------------------------------------------
    # Construction depuis config.json
    # --------------------------------------------------------
    @classmethod
    def from_config(
        cls,
        raw_cfg: Dict[str, Any],
        logger: Optional[logging.Logger] = None,
    ) -> "RuntimeWalletManager":
        """
        Construit un WalletFlowsEngine depuis config.json,
        puis le wrap dans un RuntimeWalletManager.
        """
        engine_logger = logger.getChild("WalletFlowsEngine") if logger else None
        engine = build_wallet_engine_from_config(raw_cfg, logger=engine_logger)
        return cls(engine=engine, logger=logger)

    # --------------------------------------------------------
    # Interface runtime
    # --------------------------------------------------------
    def refresh_balances(self) -> None:
        """No-op pour l’instant en PAPER_ONCHAIN."""
        return None

    def on_tick(self) -> None:
        """
        Appelé à chaque tick par le runtime.
        """
        self._engine.run_periodic_tasks()
        self._persist_snapshot()

    def on_trade_closed(self, wallet_id: str, pnl_usd: Decimal) -> None:
        """
        Appelé par l’ExecutionEngine à chaque fermeture de trade.
        """
        self._logger.info(
            "RuntimeWalletManager.on_trade_closed — wallet=%s pnl_usd=%s",
            wallet_id,
            str(pnl_usd),
        )

        if hasattr(self._engine, "apply_realized_pnl"):
            try:
                self._engine.apply_realized_pnl(wallet_id, pnl_usd)
            except Exception:
                self._logger.exception(
                    "Erreur propagation PnL vers WalletFlowsEngine "
                    "(wallet=%s, pnl_usd=%s)",
                    wallet_id,
                    str(pnl_usd),
                )
        else:
            self._logger.debug(
                "WalletFlowsEngine n'expose pas apply_realized_pnl(); "
                "PnL ignoré pour l'instant."
            )

        self._persist_snapshot()

    # --------------------------------------------------------
    # Helpers de lecture (RiskEngine / debug)
    # --------------------------------------------------------
    @property
    def engine(self) -> WalletFlowsEngine:
        return self._engine

    @property
    def flows_engine(self) -> WalletFlowsEngine:
        return self._engine

    def get_flows_engine(self) -> WalletFlowsEngine:
        return self._engine

    def debug_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        Snapshot lisible de l'état des wallets (balances/pnl du jour/etc.).

        Retourne un dict:
        { "wallet_id": { "balance_usd": ..., ... }, ... }
        """
        try:
            snap = self._engine.debug_snapshot()
            if not isinstance(snap, dict):
                self._logger.warning(
                    "WalletFlowsEngine.debug_snapshot() n'a pas renvoyé un dict: %r",
                    snap,
                )
                return {}
            return snap
        except Exception:
            self._logger.exception(
                "RuntimeWalletManager.debug_snapshot: erreur lors de l'appel "
                "à WalletFlowsEngine.debug_snapshot()"
            )
            return {}

    def get_all_wallet_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return self.debug_snapshot()

    def get_wallet_snapshot(self, wallet_id: str) -> Dict[str, Any]:
        return self.debug_snapshot().get(wallet_id, {})

    def get_total_equity_usd(self) -> Decimal:
        snap = self.debug_snapshot()
        total = Decimal("0")
        for w in snap.values():
            bal = w.get("balance_usd")
            if bal is None:
                continue
            total += Decimal(str(bal))
        return total

    def get_wallet_equity_usd(self, wallet_id: str) -> Decimal:
        w = self.get_wallet_snapshot(wallet_id)
        bal = w.get("balance_usd")
        if bal is None:
            return Decimal("0")
        return Decimal(str(bal))

    def get_global_pnl_today_usd(self) -> Decimal:
        snap = self.debug_snapshot()
        total = Decimal("0")
        for w in snap.values():
            pnl = w.get("realized_pnl_today_usd") or w.get("pnl_today_usd")
            if pnl is None:
                continue
            total += Decimal(str(pnl))
        return total

    def get_wallet_pnl_today_usd(self, wallet_id: str) -> Decimal:
        w = self.get_wallet_snapshot(wallet_id)
        pnl = w.get("realized_pnl_today_usd") or w.get("pnl_today_usd")
        if pnl is None:
            return Decimal("0")
        return Decimal(str(pnl))

    # --------------------------------------------------------
    # Persistance pour le dashboard
    # --------------------------------------------------------
    def _persist_snapshot(self) -> None:
        """
        Écrit data/godmode/wallets_runtime.json

        {
          "updated_at": "...",
          "wallets": { "sniper_sol": {...}, ... },
          "wallets_count": 10,
          "equity_total_usd": 150.0,
          "pnl_day": {
            "total_realized_usd": 0.0,
            "total_fees_usd": 0.0
          }
        }
        """
        try:
            GODMODE_DIR.mkdir(parents=True, exist_ok=True)
            snapshot = self.debug_snapshot() or {}

            # Nombre de wallets
            wallets_count = len(snapshot)

            # Equity totale + PnL agrégé du jour
            equity_total = Decimal("0")
            pnl_total = Decimal("0")

            for w in snapshot.values():
                bal = w.get("balance_usd")
                if bal is not None:
                    try:
                        equity_total += Decimal(str(bal))
                    except Exception:
                        self._logger.warning(
                            "RuntimeWalletManager: balance_usd invalide dans snapshot: %r",
                            bal,
                        )

                pnl = w.get("realized_pnl_today_usd") or w.get("pnl_today_usd")
                if pnl is not None:
                    try:
                        pnl_total += Decimal(str(pnl))
                    except Exception:
                        self._logger.warning(
                            "RuntimeWalletManager: pnl_today invalide dans snapshot: %r",
                            pnl,
                        )

            pnl_day = {
                "total_realized_usd": float(pnl_total),
                # Pour M10 : on ne branche pas encore un vrai tracking des fees par wallet,
                # on garde un placeholder neutre.
                "total_fees_usd": 0.0,
            }

            payload: Dict[str, Any] = {
                "updated_at": datetime.utcnow().isoformat(),
                "wallets": snapshot,
                "wallets_count": wallets_count,
                "equity_total_usd": float(equity_total),
                "pnl_day": pnl_day,
            }

            with _SNAPSHOT_LOCK:
                RUNTIME_WALLETS_PATH.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )

            self._logger.info(
                "RuntimeWalletManager: snapshot persisté — path=%s wallets=%d equity_total_usd=%s pnl_total_usd=%s",
                str(RUNTIME_WALLETS_PATH),
                wallets_count,
                str(equity_total),
                str(pnl_total),
            )
        except Exception:
            self._logger.exception(
                "RuntimeWalletManager: échec de la persistance de wallets_runtime.json"
            )


