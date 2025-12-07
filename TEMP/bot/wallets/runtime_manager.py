from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from .factory import build_wallet_engine_from_config
from .engine import WalletFlowsEngine
from .models import WalletState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths de base (compat MemecoinRuntime + dashboard)
# ---------------------------------------------------------------------------

# Chemin de la racine du projet BOT_GODMODE (…/BOT_GODMODE/BOT_GODMODE)
BASE_DIR: Path = Path(__file__).resolve().parents[2]

# Dossier data GODMODE + fichier wallets_runtime.json
DATA_DIR: Path = BASE_DIR / "data" / "godmode"
RUNTIME_WALLETS_PATH: Path = DATA_DIR / "wallets_runtime.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _safe_decimal(x: Any) -> Decimal:
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")


# ---------------------------------------------------------------------------
# RuntimeWalletManager
# ---------------------------------------------------------------------------


class RuntimeWalletManager:
    """
    Façade runtime pour l'engine de wallets (M10 / LIVE_150).

    Rôle :
    - construire un WalletFlowsEngine depuis config.json,
    - recevoir les événements runtime (tick, trade fermé),
    - maintenir un snapshot JSON "dashboard-friendly" dans
      data/godmode/wallets_runtime.json,
    - exposer les métriques globales attendues par le RiskEngine
      (WalletMetricsIface).

    Ce module NE fait que de la compta interne (paper) : aucun mouvement
    on-chain réel.
    """

    # ------------------------------------------------------------------
    # Construction / init
    # ------------------------------------------------------------------

    def __init__(
        self,
        engine: Optional[WalletFlowsEngine],
        *,
        profile_id: str = "LIVE_150",
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        self._engine: Optional[WalletFlowsEngine] = engine
        self._profile_id = profile_id or "LIVE_150"
        self._logger = logger_ or logging.getLogger("RuntimeWalletManager")
        self._last_snapshot: Dict[str, Any] = {}

        if self._engine is None:
            self._logger.warning(
                "RuntimeWalletManager initialisé SANS WalletFlowsEngine "
                "(mode dégradé : pas de mise à jour wallets_runtime.json)."
            )
        else:
            self._logger.info(
                "RuntimeWalletManager initialisé avec WalletFlowsEngine "
                "(profile_id=%s).",
                self._profile_id,
            )

    @classmethod
    def from_config(
        cls,
        raw_cfg: Dict[str, Any],
        *,
        logger: Optional[logging.Logger] = None,
    ) -> "RuntimeWalletManager":
        """
        Construit un RuntimeWalletManager depuis la config complète.

        - essaye de construire un WalletFlowsEngine via
          build_wallet_engine_from_config,
        - si échec → mode dégradé (engine=None) mais sans casser le runtime.
        """
        log = logger or logging.getLogger("RuntimeWalletManager")

        # Profil logique (LIVE_150 figé pour M10, mais on reste tolérant)
        finance_cfg = raw_cfg.get("finance") or {}
        profile_id = (
            raw_cfg.get("profile_id")
            or finance_cfg.get("profile_id")
            or raw_cfg.get("profile")
            or "LIVE_150"
        )

        engine: Optional[WalletFlowsEngine]
        try:
            engine = build_wallet_engine_from_config(raw_cfg, logger=log)
        except Exception as exc:
            log.exception(
                "RuntimeWalletManager.from_config: impossible de construire "
                "WalletFlowsEngine depuis config.json (mode dégradé).\nErr=%s",
                exc,
            )
            engine = None

        mgr = cls(engine, profile_id=profile_id, logger_=log)

        # Premier snapshot dès l'init (si engine OK)
        try:
            mgr._write_snapshot()
        except Exception:
            log.exception(
                "RuntimeWalletManager.from_config: échec écriture snapshot initial."
            )

        return mgr

    # ------------------------------------------------------------------
    # Exposition du WalletFlowsEngine interne (pour FinanceEngine, debug)
    # ------------------------------------------------------------------

    @property
    def engine(self) -> Optional[WalletFlowsEngine]:
        """Accès read-only au WalletFlowsEngine interne."""
        return self._engine

    def get_engine(self) -> Optional[WalletFlowsEngine]:
        """Getter compatible avec _extract_wallet_flows_engine."""
        return self._engine

    # ------------------------------------------------------------------
    # Interface WalletManagerIface (appelée par BotRuntime)
    # ------------------------------------------------------------------

    def refresh_balances(self) -> None:
        """
        Hook d'interface WalletManagerIface appelé par BotRuntime au
        début de chaque tick.

        En M10 / PAPER_ONCHAIN, les soldes sont purement comptables
        (WalletFlowsEngine) et il n'y a pas encore de lecture on-chain.

        Cette méthode existe pour :
        - respecter le contrat WalletManagerIface,
        - préparer le passage au vrai LIVE où l'on branchera ici le
          refresh des soldes on-chain via les RPC.
        """
        if self._engine is None:
            # Mode dégradé : rien à faire, mais on ne casse pas le runtime.
            self._logger.debug(
                "RuntimeWalletManager.refresh_balances: engine=None, skip."
            )
            return

        # M10 : aucun refresh on-chain, on laisse la compta interne faire le job.
        self._logger.debug(
            "RuntimeWalletManager.refresh_balances: PAPER_ONCHAIN, "
            "pas de refresh on-chain (balances internes)."
        )

    def on_tick(self) -> None:
        """Tick périodique (appelé par BotRuntime)."""
        if self._engine is None:
            # Mode dégradé : rien à faire, mais on ne casse pas le runtime.
            self._logger.debug("RuntimeWalletManager.on_tick: engine=None, skip.")
            return

        try:
            # Reset journalier + hooks finance (auto-fees, profit splits, caps…)
            self._engine.run_periodic_tasks()
            self._write_snapshot()
        except Exception as exc:
            self._logger.exception(
                "RuntimeWalletManager.on_tick: erreur lors du tick finance (%s).",
                exc,
            )

    def on_trade_closed(
        self,
        wallet_id: str,
        realized_pnl_usd: Decimal,
        fees_paid_usd: Decimal = Decimal("0"),
    ) -> None:
        """
        Callback à appeler après la clôture d'un trade.

        `wallet_id` est le nom logique (ex: "sniper_sol", "copy_sol", "fees"...).
        `realized_pnl_usd` peut être positif ou négatif.
        `fees_paid_usd` est optionnel (>= 0).
        """
        if self._engine is None:
            self._logger.debug(
                "RuntimeWalletManager.on_trade_closed: engine=None, skip "
                "(wallet_id=%s, pnl=%s).",
                wallet_id,
                realized_pnl_usd,
            )
            return

        try:
            self._engine.apply_realized_pnl(
                wallet_id=wallet_id,
                realized_pnl_usd=realized_pnl_usd,
                fees_paid_usd=fees_paid_usd,
            )
            self._write_snapshot()
        except Exception as exc:
            self._logger.exception(
                "RuntimeWalletManager.on_trade_closed: erreur lors de la "
                "mise à jour du wallet %s (pnl=%s, fees=%s) : %s",
                wallet_id,
                realized_pnl_usd,
                fees_paid_usd,
                exc,
            )

    # ------------------------------------------------------------------
    # Interface WalletMetricsIface (consommée par RiskEngine)
    # ------------------------------------------------------------------

    def get_total_equity_usd(self) -> Decimal:
        """
        Equity globale actuelle (somme des balances de tous les wallets).

        Utilisée par le RiskEngine pour calculer le drawdown global.
        """
        if self._engine is not None:
            total = Decimal("0")
            for state in self._engine.states.values():
                total += state.balance_usd
            return total

        # Fallback : on lit le dernier snapshot (ou le fichier JSON)
        snap = self._last_snapshot or self._build_snapshot()
        wallets = snap.get("wallets") or {}
        total = Decimal("0")
        if isinstance(wallets, dict):
            for w in wallets.values():
                bal = w.get("balance_usd")
                total += _safe_decimal(bal)
        else:
            total = _safe_decimal(snap.get("equity_total_usd", 0.0))

        return total

    def get_global_pnl_today_usd(self) -> Decimal:
        """
        PnL global du jour (tous wallets confondus), en USD.

        En M10 on s'aligne sur gross_pnl_today_usd des WalletState.
        """
        if self._engine is not None:
            total = Decimal("0")
            for state in self._engine.states.values():
                total += state.gross_pnl_today_usd
            return total

        # Fallback : on lit le dernier snapshot (ou le fichier JSON)
        snap = self._last_snapshot or self._build_snapshot()

        # On essaye d'abord le champ agrégé top-level…
        if "pnl_today_total_usd" in snap:
            return _safe_decimal(snap.get("pnl_today_total_usd", 0.0))

        # … sinon on recalcule à partir des wallets.
        wallets = snap.get("wallets") or {}
        total = Decimal("0")
        if isinstance(wallets, dict):
            for w in wallets.values():
                pnl = w.get("gross_pnl_today_usd")
                total += _safe_decimal(pnl)

        return total

    # ------------------------------------------------------------------
    # Snapshot wallets_runtime.json
    # ------------------------------------------------------------------

    def _build_snapshot_from_engine(self) -> Dict[str, Any]:
        """Construit le snapshot complet à partir du WalletFlowsEngine."""
        assert self._engine is not None

        states: Dict[str, WalletState] = self._engine.states
        wallets: Dict[str, Any] = {}

        equity_total = Decimal("0")
        realized_total = Decimal("0")
        gross_total = Decimal("0")
        fees_total = Decimal("0")

        for wid, state in states.items():
            equity_total += state.balance_usd
            realized_total += state.realized_pnl_today_usd
            gross_total += state.gross_pnl_today_usd
            fees_total += state.fees_paid_today_usd

            wallets[wid] = {
                "balance_usd": str(state.balance_usd),
                "realized_pnl_today_usd": str(state.realized_pnl_today_usd),
                "gross_pnl_today_usd": str(state.gross_pnl_today_usd),
                "fees_paid_today_usd": str(state.fees_paid_today_usd),
                "consecutive_losing_trades": str(
                    state.consecutive_losing_trades
                ),
                "last_reset_date": state.last_reset_date.isoformat(),
            }

        snapshot: Dict[str, Any] = {
            "updated_at": _now_iso(),
            "wallets_source": "runtime_manager",
            "wallets": wallets,
            "wallets_count": len(wallets),
            # equity & PnL top-level (floats pour compat dashboard)
            "equity_total_usd": _safe_float(equity_total),
            "pnl_total_usd": _safe_float(gross_total),
            "pnl_today_total_usd": _safe_float(gross_total),
            "pnl_day": {
                "total_realized_usd": _safe_float(realized_total),
                "total_fees_usd": _safe_float(fees_total),
            },
        }

        if self._profile_id:
            snapshot["profile_id"] = self._profile_id

        return snapshot

    def _build_snapshot(self) -> Dict[str, Any]:
        """Construit le snapshot complet (engine ou fallback)."""
        if self._engine is None:
            # Mode dégradé : on réutilise le fichier existant si possible,
            # sinon on crée un squelette minimal.
            path = RUNTIME_WALLETS_PATH
            base: Dict[str, Any] = {}

            if path.exists():
                try:
                    base = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    base = {}

            if not isinstance(base, dict):
                base = {}

            base.setdefault("wallets", {})
            base.setdefault("wallets_count", len(base.get("wallets") or {}))
            base["updated_at"] = _now_iso()
            base.setdefault("equity_total_usd", 0.0)
            base.setdefault("pnl_total_usd", 0.0)
            base.setdefault(
                "pnl_day",
                {"total_realized_usd": 0.0, "total_fees_usd": 0.0},
            )

            if self._profile_id:
                base.setdefault("profile_id", self._profile_id)

            base.setdefault("wallets_source", "runtime_manager_stub")
            return base

        return self._build_snapshot_from_engine()

    def _write_snapshot(self) -> None:
        """Écrit le snapshot courant dans wallets_runtime.json (écriture safe)."""
        snapshot = self._build_snapshot()
        self._last_snapshot = snapshot

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        tmp_path = RUNTIME_WALLETS_PATH.with_suffix(
            RUNTIME_WALLETS_PATH.suffix + ".tmp"
        )
        tmp_path.write_text(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        tmp_path.replace(RUNTIME_WALLETS_PATH)

        self._logger.debug(
            "RuntimeWalletManager: snapshot écrit dans %s (wallets=%d, "
            "equity=%.2f, pnl=%.2f).",
            str(RUNTIME_WALLETS_PATH),
            snapshot.get("wallets_count"),
            snapshot.get("equity_total_usd"),
            snapshot.get("pnl_today_total_usd"),
        )

    # ------------------------------------------------------------------
    # Helpers de debug
    # ------------------------------------------------------------------

    def get_last_snapshot(self) -> Dict[str, Any]:
        """Retourne le dernier snapshot construit en mémoire (debug)."""
        return dict(self._last_snapshot)

