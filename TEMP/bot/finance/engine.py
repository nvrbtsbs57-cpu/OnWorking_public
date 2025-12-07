# bot/finance/engine.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Mapping

from bot.wallets.engine import WalletFlowsEngine
from bot.wallets.models import WalletRole, WalletState, WalletConfig

from bot.finance.pipeline import (
    FinancePipeline,
    WalletSnapshot,
    TransferPlan,
)

logger = logging.getLogger(__name__)


# ======================================================================
# Config & dataclasses "vue finance"
# ======================================================================


@dataclass
class FinanceEngineConfig:
    """
    Config "light" pour le moteur finance.

    Pour l'instant, on ne déplace PAS la logique d'auto-fees / profit-split
    hors du WalletFlowsEngine (M4-core reste la source de vérité).

    Cette config sert surtout à :
      - activer/désactiver certains agrégats / hooks,
      - préparer le terrain pour M4-full (sweeps réels, limites, etc.).
    """

    enable_auto_fees: bool = True
    enable_profit_split: bool = True
    enable_compounding: bool = False  # compounding réel sera géré plus tard ici


@dataclass
class WalletFinanceView:
    """
    Vue finance simplifiée pour un wallet (pour dashboard / monitoring).
    """

    wallet_id: str
    role: WalletRole
    balance_usd: Decimal
    realized_pnl_today_usd: Decimal
    gross_pnl_today_usd: Decimal
    fees_paid_today_usd: Decimal


@dataclass
class FinanceSnapshot:
    """
    Snapshot agrégé de l'état finance du système à un instant T.
    """

    as_of: datetime
    total_equity_usd: Decimal
    total_pnl_today_usd: Decimal
    total_fees_today_usd: Decimal
    wallets: List[WalletFinanceView]
    equity_by_role: Dict[WalletRole, Decimal]
    pnl_today_by_role: Dict[WalletRole, Decimal]
    fees_today_by_role: Dict[WalletRole, Decimal]


# ======================================================================
# FinanceEngine : orchestration + métriques + pont vers FinancePipeline
# ======================================================================


class FinanceEngine:
    """
    Orchestrateur Finance (M4) au-dessus de WalletFlowsEngine.

    Rôles principaux à ce stade (M4-core+) :
      - exposer des métriques agrégées (par wallet, par rôle) pour le monitoring,
      - servir de pont vers FinancePipeline (on-chain TransferPlan),
      - fournir des hooks propres pour des opérations finance plus avancées
        (sweeps, compounding réel, payout…) à implémenter en M4-full,
      - NE PAS dupliquer la logique déjà présente dans WalletFlowsEngine
        (auto-fees / profit-split / transferts internes papier).

    Important :
      - WalletFlowsEngine = source de vérité "paper" pour :
        * balances,
        * PnL du jour,
        * fees du jour,
        * rôles & limites de wallets.
      - FinancePipeline = moteur de *planning on-chain* générique (autofees,
        sweep, compounding) qui travaille sur des WalletSnapshot.
    """

    def __init__(
        self,
        wallet_engine: WalletFlowsEngine,
        cfg: Optional[FinanceEngineConfig] = None,
        pipeline: Optional[FinancePipeline] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._wallet_engine = wallet_engine
        self._cfg = cfg or FinanceEngineConfig()
        self._pipeline = pipeline
        self._logger = logger or logging.getLogger(__name__)

        self._logger.info(
            "FinanceEngine initialisé "
            "(enable_auto_fees=%s, enable_profit_split=%s, enable_compounding=%s, pipeline_attached=%s)",
            self._cfg.enable_auto_fees,
            self._cfg.enable_profit_split,
            self._cfg.enable_compounding,
            bool(self._pipeline),
        )

    # ------------------------------------------------------------------
    # Gestion du FinancePipeline (on-chain planning)
    # ------------------------------------------------------------------

    def attach_pipeline(self, pipeline: FinancePipeline) -> None:
        """
        Attache ou remplace le FinancePipeline utilisé pour planifier
        les transferts on-chain (autofees, sweep, compounding).

        On ne crée PAS le pipeline ici : il est construit ailleurs
        (à partir de config.json) puis injecté.
        """
        self._pipeline = pipeline
        self._logger.info(
            "FinanceEngine.attach_pipeline() — FinancePipeline attaché (autofees=%s, sweep=%s, compounding=%s)",
            pipeline.config.autofees.enabled,
            pipeline.config.sweep.enabled,
            pipeline.config.compounding.enabled,
        )

    def plan_onchain_transfers(
        self,
        snapshots: Mapping[str, WalletSnapshot],
        mode: str = "all",
    ) -> List[TransferPlan]:
        """
        Passe-plat vers FinancePipeline pour générer des TransferPlan on-chain.

        - mode="autofees"   -> FinancePipeline.plan_autofees(...)
        - mode="sweep"      -> FinancePipeline.plan_sweep_profits(...)
        - mode="compound"   -> FinancePipeline.plan_compounding(...)
        - mode="all" (def.) -> FinancePipeline.plan_all(...)

        Ce moteur NE construit PAS les WalletSnapshot : ils doivent être
        préparés par la couche qui connaît les wallets on-chain (RPC, etc.).
        """
        if not self._pipeline:
            self._logger.debug(
                "FinanceEngine.plan_onchain_transfers() — aucun FinancePipeline attaché, retour []. mode=%s",
                mode,
            )
            return []

        if mode == "autofees":
            plans = self._pipeline.plan_autofees(snapshots)
        elif mode == "sweep":
            plans = self._pipeline.plan_sweep_profits(snapshots)
        elif mode == "compound":
            plans = self._pipeline.plan_compounding(snapshots)
        else:
            plans = self._pipeline.plan_all(snapshots)

        self._logger.debug(
            "FinanceEngine.plan_onchain_transfers() — mode=%s, plans=%d",
            mode,
            len(plans),
        )
        return plans

    # ------------------------------------------------------------------
    # Hooks runtime (pour l’instant no-op : on laisse WFE faire le boulot)
    # ------------------------------------------------------------------

    def on_tick(self, now: Optional[datetime] = None) -> None:
        """
        Hook appelé périodiquement par le runtime.

        Pour l’instant (M4-core+), ce hook ne fait que logguer.
        La logique opérationnelle d’auto-fees / profit-split reste dans
        WalletFlowsEngine.run_periodic_tasks() et dans register_fill().

        En M4-full, c’est ici qu’on orchestrera :
          - sweeps vers vault/payout/emergency (via FinancePipeline),
          - compounding réel,
          - limites de transferts inter-wallets on-chain, etc.
        """
        now = now or datetime.utcnow()

        # On se contente d'un debug pour l'instant pour éviter tout double-run.
        self._logger.debug(
            "FinanceEngine.on_tick() — no-op (M4-core, orchestration avancée à venir). now=%s",
            now.isoformat(),
        )

    # ------------------------------------------------------------------
    # Métriques agrégées pour le dashboard finance (papier)
    # ------------------------------------------------------------------

    def build_snapshot(self, now: Optional[datetime] = None) -> FinanceSnapshot:
        """
        Construit un snapshot complet de l'état finance (papier) à un instant T.

        Utilisable directement par la couche Monitoring / UI.
        """
        now = now or datetime.utcnow()

        configs: Dict[str, WalletConfig] = self._wallet_engine.configs
        states: Dict[str, WalletState] = self._wallet_engine.states

        wallets_view: List[WalletFinanceView] = []

        total_equity = Decimal("0")
        total_pnl_today = Decimal("0")
        total_fees_today = Decimal("0")

        equity_by_role: Dict[WalletRole, Decimal] = {}
        pnl_by_role: Dict[WalletRole, Decimal] = {}
        fees_by_role: Dict[WalletRole, Decimal] = {}

        for wid, state in states.items():
            cfg = configs.get(wid)
            if cfg is None:
                # Incohérence théorique : on log et on skip
                self._logger.warning(
                    "FinanceEngine.build_snapshot() — config manquante pour wallet_id=%s",
                    wid,
                )
                continue

            view = WalletFinanceView(
                wallet_id=wid,
                role=cfg.role,
                balance_usd=state.balance_usd,
                realized_pnl_today_usd=state.realized_pnl_today_usd,
                gross_pnl_today_usd=state.gross_pnl_today_usd,
                fees_paid_today_usd=state.fees_paid_today_usd,
            )
            wallets_view.append(view)

            # Totaux globaux
            total_equity += state.balance_usd
            total_pnl_today += state.gross_pnl_today_usd
            total_fees_today += state.fees_paid_today_usd

            # Agrégats par rôle
            equity_by_role[cfg.role] = equity_by_role.get(cfg.role, Decimal("0")) + state.balance_usd
            pnl_by_role[cfg.role] = pnl_by_role.get(cfg.role, Decimal("0")) + state.gross_pnl_today_usd
            fees_by_role[cfg.role] = fees_by_role.get(cfg.role, Decimal("0")) + state.fees_paid_today_usd

        snapshot = FinanceSnapshot(
            as_of=now,
            total_equity_usd=total_equity,
            total_pnl_today_usd=total_pnl_today,
            total_fees_today_usd=total_fees_today,
            wallets=wallets_view,
            equity_by_role=equity_by_role,
            pnl_today_by_role=pnl_by_role,
            fees_today_by_role=fees_by_role,
        )

        self._logger.debug(
            "FinanceEngine.build_snapshot() — total_equity=%s total_pnl_today=%s total_fees_today=%s",
            str(total_equity),
            str(total_pnl_today),
            str(total_fees_today),
        )

        return snapshot

    # ------------------------------------------------------------------
    # Helpers de lecture rapide (sugar pour le runtime / monitoring)
    # ------------------------------------------------------------------

    def get_total_equity_usd(self) -> Decimal:
        """
        Retourne l'equity totale en USD (somme des balances des wallets papier).
        """
        total = sum(
            (state.balance_usd for state in self._wallet_engine.states.values()),
            start=Decimal("0"),
        )
        self._logger.debug("FinanceEngine.get_total_equity_usd() => %s", str(total))
        return total

    def get_total_pnl_today_usd(self) -> Decimal:
        """
        Retourne le PnL global du jour (gross, net des fees si on veut plus tard).
        """
        total = sum(
            (state.gross_pnl_today_usd for state in self._wallet_engine.states.values()),
            start=Decimal("0"),
        )
        self._logger.debug("FinanceEngine.get_total_pnl_today_usd() => %s", str(total))
        return total

    def get_equity_by_role(self) -> Dict[WalletRole, Decimal]:
        """
        Equity agrégée par rôle de wallet (TRADE_MEMECOINS, COPY_TRADING, VAULT, FEES, etc.).
        """
        result: Dict[WalletRole, Decimal] = {}
        for wid, state in self._wallet_engine.states.items():
            cfg = self._wallet_engine.configs.get(wid)
            if cfg is None:
                continue
            result[cfg.role] = result.get(cfg.role, Decimal("0")) + state.balance_usd

        self._logger.debug(
            "FinanceEngine.get_equity_by_role() => %s",
            {str(role): str(val) for role, val in result.items()},
        )
        return result

    def get_pnl_today_by_role(self) -> Dict[WalletRole, Decimal]:
        """
        PnL du jour (gross) agrégé par rôle de wallet.
        """
        result: Dict[WalletRole, Decimal] = {}
        for wid, state in self._wallet_engine.states.items():
            cfg = self._wallet_engine.configs.get(wid)
            if cfg is None:
                continue
            result[cfg.role] = result.get(cfg.role, Decimal("0")) + state.gross_pnl_today_usd

        self._logger.debug(
            "FinanceEngine.get_pnl_today_by_role() => %s",
            {str(role): str(val) for role, val in result.items()},
        )
        return result

    def get_fees_today_by_role(self) -> Dict[WalletRole, Decimal]:
        """
        Fees payées aujourd'hui agrégées par rôle de wallet.
        """
        result: Dict[WalletRole, Decimal] = {}
        for wid, state in self._wallet_engine.states.items():
            cfg = self._wallet_engine.configs.get(wid)
            if cfg is None:
                continue
            result[cfg.role] = result.get(cfg.role, Decimal("0")) + state.fees_paid_today_usd

        self._logger.debug(
            "FinanceEngine.get_fees_today_by_role() => %s",
            {str(role): str(val) for role, val in result.items()},
        )
        return result
