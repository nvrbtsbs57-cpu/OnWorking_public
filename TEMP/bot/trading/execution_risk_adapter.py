# bot/trading/execution_risk_adapter.py

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from bot.execution.engine import ExecutionEngine, ExecutionRequest, ExecutionResult
from bot.core.risk import RiskEngine, RiskDecision  # à adapter si besoin

logger = logging.getLogger(__name__)


# ============================================================================
# KillSwitchState
# ============================================================================

@dataclass
class KillSwitchState:
    """
    Kill-switch runtime pour l'exécution.

    - enabled : permet d'activer/désactiver complètement le kill-switch.
    - trip_on_risk_eject : si True, un RiskDecision.EJECT déclenche un trip auto.
    - manual_tripped : kill-switch trippé manuellement (admin / API).
    - risk_tripped   : kill-switch trippé par la logique de risk.
    - last_trip_reason : texte expliquant la dernière raison du trip.
    """
    enabled: bool = True
    trip_on_risk_eject: bool = True
    manual_tripped: bool = False

    # champs purement runtime (non lus dans la config)
    risk_tripped: bool = False
    last_trip_reason: Optional[str] = None

    @property
    def tripped(self) -> bool:
        return self.manual_tripped or self.risk_tripped

    def trip(self, reason: str, *, from_risk: bool = False) -> None:
        """
        Trippe le kill-switch (manuel ou depuis le moteur de risk).
        """
        if not self.enabled:
            return

        if from_risk:
            self.risk_tripped = True
        else:
            self.manual_tripped = True

        self.last_trip_reason = reason
        logger.warning(
            "[KillSwitchState] Kill-switch TRIPPED (from_risk=%s, reason=%s)",
            from_risk,
            reason,
        )

    def reset_for_new_day(self) -> None:
        """
        Reset à appeler uniquement:
        - au changement de journée (logic/date),
        - ou via une action admin explicite.

        Pas de reset automatique intra-journée.
        """
        self.risk_tripped = False
        self.manual_tripped = False
        self.last_trip_reason = None
        logger.info("[KillSwitchState] Reset complet pour nouvelle journée.")


# ============================================================================
# RuntimeWalletStats
# ============================================================================

@dataclass
class RuntimeWalletStats:
    """
    Petit wrapper pour fournir au RiskEngine un snapshot runtime
    (équity globale, PnL du jour, losing streak, etc.).

    On suppose que le RuntimeWalletManager expose une méthode
    `get_risk_snapshot()` ou similaire. Adapte cette partie à ton implémentation.
    """
    wallet_manager: Any

    def snapshot(self) -> Dict[str, Any]:
        try:
            # Idéal: une méthode dédiée sur RuntimeWalletManager
            return self.wallet_manager.get_risk_snapshot()
        except AttributeError:
            # Fallback minimal: on renvoie un dict très simple
            return {
                "equity_total_usd": getattr(
                    self.wallet_manager, "equity_total_usd", None
                )
            }


# ============================================================================
# ExecutionRiskAdapter
# ============================================================================

@dataclass
class ExecutionRiskAdapter:
    """
    Adapter autour d'un ExecutionEngine qui ajoute:

    - RiskEngine (décision avant exécution),
    - KillSwitchState (blocage dur de la journée),
    - métriques runtime (RuntimeWalletStats).

    C'est CET objet que l'AgentEngine doit utiliser, pas ExecutionEngine
    directement.
    """
    inner_engine: ExecutionEngine
    stats_provider: RuntimeWalletStats
    risk_engine: Optional[RiskEngine] = None
    enabled: bool = True
    kill_switch: Optional[KillSwitchState] = None

    # ------------------------------------------------------------------ #
    # API principale
    # ------------------------------------------------------------------ #
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        """
        Point d'entrée unique pour exécuter un ordre avec risk + kill-switch.
        """

        # 0) Si le risk global est désactivé, on laisse passer en direct.
        if not self.enabled or self.risk_engine is None:
            return self.inner_engine.execute(req)

        # 1) Kill-switch déjà trippé ?
        if self._is_kill_switch_blocking():
            reason = (
                self.kill_switch.last_trip_reason
                if self.kill_switch and self.kill_switch.last_trip_reason
                else "KILL_SWITCH_TRIPPED"
            )
            logger.warning(
                "[ExecutionRiskAdapter] Ordre refusé (kill-switch déjà trippé): %s",
                reason,
            )
            return ExecutionResult(
                success=False,
                reason=f"KILL_SWITCH_TRIPPED:{reason}",
            )

        # 2) Snapshot runtime pour le RiskEngine
        stats = self._safe_get_stats()

        # 3) Décision du RiskEngine
        decision, detail = self._call_risk_engine(req, stats)

        # 4) Traitement de la décision
        if decision == RiskDecision.REJECT:
            # Cas: ordre jugé trop risqué mais pas "fin de journée"
            logger.info(
                "[ExecutionRiskAdapter] RiskEngine REJECT l'ordre: %s", detail
            )
            return ExecutionResult(
                success=False,
                reason=f"RISK_REJECT:{detail or ''}",
            )

        if decision == RiskDecision.EJECT:
            # Cas: CRITICAL “jour” → fin de journée
            logger.warning(
                "[ExecutionRiskAdapter] RiskEngine EJECT: CRITICAL jour (%s)",
                detail,
            )
            if self.kill_switch and self.kill_switch.enabled and self.kill_switch.trip_on_risk_eject:
                self.kill_switch.trip(
                    reason=detail or "RiskDecision.EJECT",
                    from_risk=True,
                )
            return ExecutionResult(
                success=False,
                reason=f"RISK_EJECT:{detail or ''}",
            )

        # 5) ACCEPT → on exécute réellement l'ordre
        result = self.inner_engine.execute(req)

        # 6) Notification post-trade au RiskEngine (pour mettre à jour PnL, streak, etc.)
        self._notify_risk_engine_post_trade(req, result)

        return result

    # ------------------------------------------------------------------ #
    # Helpers internes
    # ------------------------------------------------------------------ #

    def _is_kill_switch_blocking(self) -> bool:
        ks = self.kill_switch
        if ks is None or not ks.enabled:
            return False
        return ks.tripped

    def _safe_get_stats(self) -> Dict[str, Any]:
        try:
            return self.stats_provider.snapshot()
        except Exception as exc:
            logger.exception(
                "[ExecutionRiskAdapter] Erreur lors de la récupération du snapshot runtime",
                exc_info=exc,
            )
            return {}

    def _call_risk_engine(
        self,
        req: ExecutionRequest,
        stats: Dict[str, Any],
    ) -> tuple[RiskDecision, Optional[str]]:
        """
        Appelle le RiskEngine de manière défensive.

        On suppose une API de type:
            decision, detail = risk_engine.decide_for_execution(req=req, stats=stats)

        Adapte cette méthode si ton RiskEngine a une signature différente.
        """
        try:
            decision, detail = self.risk_engine.decide_for_execution(
                req=req,
                stats=stats,
            )
        except TypeError:
            # Variante si tu as un vieux nom de méthode
            decision, detail = self.risk_engine.check_execution_request(
                req=req,
                stats=stats,
            )
        except Exception as exc:
            logger.exception(
                "[ExecutionRiskAdapter] Erreur RiskEngine.decide_for_execution, "
                "fallback ACCEPT (non-bloquant pour éviter un dead-bot).",
                exc_info=exc,
            )
            return RiskDecision.ACCEPT, "risk_engine_error"

        return decision, detail

    def _notify_risk_engine_post_trade(
        self,
        req: ExecutionRequest,
        result: ExecutionResult,
    ) -> None:
        """
        Permet au RiskEngine de mettre à jour ses compteurs (Pnl du jour,
        losing streak, etc.) après chaque exécution.
        """
        try:
            # Signature proposée : à adapter si besoin.
            self.risk_engine.on_execution_result(
                req=req,
                result=result,
            )
        except AttributeError:
            # Pas implémenté, on ignore.
            return
        except Exception as exc:
            logger.exception(
                "[ExecutionRiskAdapter] Erreur lors du on_execution_result (non-bloquant).",
                exc_info=exc,
            )


__all__ = [
    "KillSwitchState",
    "RuntimeWalletStats",
    "ExecutionRiskAdapter",
]
