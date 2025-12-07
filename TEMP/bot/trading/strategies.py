from __future__ import annotations

from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any, Dict, Optional, TYPE_CHECKING

from bot.core.logging import get_logger
from .models import Signal, TradeSide
from .execution import ExecutionRequest

if TYPE_CHECKING:  # uniquement pour les types, pas d'import runtime
    from .execution import ExecutionEngine

logger = get_logger(__name__)


# ======================================================================
# Config de stratégie : comment transformer un Signal -> ExecutionRequest
# ======================================================================


@dataclass
class StrategyConfig:
    """
    Règles de base pour convertir un Signal en ExecutionRequest.

    - default_notional_usd : taille par défaut si le signal ne fournit rien
    - min_confidence       : seuil mini de confiance pour accepter un signal
    - max_notional_usd     : cap hard de taille (sécurité globale)
    - per_symbol_notional_overrides :
        permet de forcer une taille pour certains symboles.
    """

    default_notional_usd: Decimal = Decimal("100")
    min_confidence: float = 0.2
    max_notional_usd: Decimal = Decimal("10000")

    # overrides par symbole, ex: {"ETHUSDT": Decimal("250")}
    per_symbol_notional_overrides: Dict[str, Decimal] = field(default_factory=dict)

    # slippage et tag par défaut
    default_slippage_bps: int = 500
    default_strategy_tag: str = "generic"

    def resolve_notional(self, signal: Signal) -> Decimal:
        """
        Choix final de la taille en USD pour ce signal.
        """
        # 1) taille venant du signal
        notional = signal.size_usd or Decimal("0")

        # 2) si 0 ou négatif, fallback sur la taille par défaut
        if notional <= 0:
            notional = self.default_notional_usd

        # 3) override éventuel par symbole
        override = self.per_symbol_notional_overrides.get(signal.symbol)
        if override is not None and override > 0:
            notional = override

        # 4) cap global de sécurité
        if notional > self.max_notional_usd:
            notional = self.max_notional_usd

        return notional


# ======================================================================
# Résultat de la stratégie
# ======================================================================


@dataclass
class StrategyDecision:
    """
    Résultat d'une évaluation de stratégie pour un signal donné.
    """

    accepted: bool
    reason: Optional[str] = None
    execution_request: Optional[ExecutionRequest] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "execution_request": asdict(self.execution_request)
            if self.execution_request is not None
            else None,
        }


# ======================================================================
# StrategyEngine : pont Signal -> ExecutionRequest -> ExecutionEngine
# ======================================================================


class StrategyEngine:
    """
    Couche légère qui transforme les Signaux multi-chain en ExecutionRequest
    consommables par l'ExecutionEngine.

    Pipeline typique :

        Signal  --->  StrategyEngine  --->  ExecutionRequest
                                     \-->  ExecutionEngine.execute(...)
    """

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        self.config = config or StrategyConfig()
        self.logger = logger

    # ------------------------------------------------------------------
    # API principale : évaluation d'un signal
    # ------------------------------------------------------------------

    def evaluate(self, signal: Signal) -> StrategyDecision:
        """
        Applique les règles de la stratégie et, si tout est OK,
        construit un ExecutionRequest.
        """

        # 1) Vérif du niveau de confiance
        if signal.confidence < self.config.min_confidence:
            return StrategyDecision(
                accepted=False,
                reason=f"confidence<{self.config.min_confidence}",
            )

        # 2) Résolution de la taille
        notional = self.config.resolve_notional(signal)
        if notional <= 0:
            return StrategyDecision(
                accepted=False,
                reason="resolved_notional<=0",
            )

        # 3) Mapping du côté
        side = signal.side
        if side not in (TradeSide.BUY, TradeSide.SELL):
            return StrategyDecision(
                accepted=False,
                reason=f"unsupported_side:{side}",
            )

        # 4) Chain + symbol
        chain = (signal.chain or "ethereum").lower()
        symbol = signal.symbol

        # 5) Limit price + slippage
        limit_price: Optional[Decimal] = signal.entry_price
        slippage_bps = int(
            signal.meta.get("slippage_bps", self.config.default_slippage_bps)
        )

        # 6) Strategy tag
        strategy_tag = (
            signal.strategy_id
            or signal.meta.get("strategy_tag")
            or self.config.default_strategy_tag
        )

        # 7) Hints pour le routing des wallets
        #    (utilisés par ExecutionEngine via req.meta)
        wallet_role_hint = signal.meta.get("wallet_role")
        wallet_tags_hint = signal.meta.get("wallet_tags")

        meta: Dict[str, Any] = {
            "signal": signal.to_dict(),
        }
        if wallet_role_hint is not None:
            meta["prefer_wallet_role"] = wallet_role_hint
        if wallet_tags_hint is not None:
            meta["require_wallet_tags"] = wallet_tags_hint

        # 8) Construction du ExecutionRequest final
        req = ExecutionRequest(
            chain=chain,
            symbol=symbol,
            side=side,
            notional_usd=notional,
            limit_price=limit_price,
            slippage_bps=slippage_bps,
            strategy_tag=strategy_tag,
            meta=meta,
        )

        self.logger.info(
            "StrategyEngine: ExecutionRequest générée",
            extra={
                "signal_id": signal.strategy_id,
                "symbol": symbol,
                "chain": chain,
                "side": side.value,
                "notional_usd": float(notional),
                "strategy_tag": strategy_tag,
                "slippage_bps": slippage_bps,
            },
        )

        return StrategyDecision(
            accepted=True,
            reason=None,
            execution_request=req,
        )

    # ------------------------------------------------------------------
    # Helpers pratiques
    # ------------------------------------------------------------------

    def build_execution_request(self, signal: Signal) -> Optional[ExecutionRequest]:
        """
        Raccourci : renvoie directement ExecutionRequest ou None si rejet.
        """
        decision = self.evaluate(signal)
        if not decision.accepted:
            self.logger.debug(
                "StrategyEngine: signal rejeté",
                extra={"reason": decision.reason, "signal": signal.to_dict()},
            )
            return None
        return decision.execution_request

    def route_to_execution(
        self, signal: Signal, execution_engine: "ExecutionEngine"
    ) -> Optional[Any]:
        """
        Raccourci "routing complet" :
        - évalue le signal
        - si accepté, envoie l'ExecutionRequest à ExecutionEngine.execute(...)
        - renvoie le résultat d'exécution (ou None si rejet)
        """
        req = self.build_execution_request(signal)
        if req is None:
            return None
        return execution_engine.execute(req)
