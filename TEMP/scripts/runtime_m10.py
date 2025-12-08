# pseudo-code, à adapter à tes vraies classes/imports

from dataclasses import dataclass
from typing import Optional

# Types à adapter avec ton vrai code
from bot.core.signals import TradeSignal            # ton type existant
from bot.execution.types import ExecutionRequest    # bas niveau (LIVE)
from bot.execution.risk import ExecutionWithRisk    # wrapper risk+kill-switch

@dataclass
class RiskAwareMemecoinExecutor:
    exec_with_risk: ExecutionWithRisk

    def _signal_to_request(self, signal: TradeSignal) -> ExecutionRequest:
        """
        Adapte un TradeSignal memecoin → ExecutionRequest pour ExecutionWithRisk.
        Ici tu mets la vraie logique de mapping.
        """
        return ExecutionRequest(
            id=signal.id,
            wallet_id=signal.wallet_id,
            chain=signal.chain,            # champ déjà présent dans ton signal memecoin
            symbol=signal.symbol,
            side=signal.side,
            amount=signal.size,            # ou signal.amount_base / quote selon ton modèle
            # + tous les autres champs nécessaires: token_in, token_out, slippage, etc.
        )

    def execute_signal(self, signal: TradeSignal):
        """
        Interface identique à ton ancien ExecutionEngine haut niveau :
        la stratégie memecoin appelle toujours executor.execute_signal(signal),
        mais sous le capot ça passe maintenant par ExecutionWithRisk.
        """
        req = self._signal_to_request(signal)
        result = self.exec_with_risk.execute(req)
        return result

