# bot/wallet/flows.py

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .manager import WalletManager, WalletRole, WalletConfig, WalletState

logger = logging.getLogger(__name__)


# ============================================================================
# Modèles de transferts (plans, pas des vraies tx on-chain)
# ============================================================================


@dataclass
class TransferPlan:
    """
    Représente un plan de transfert interne entre wallets (en USD notionnel).

    - from_wallet / to_wallet : noms des wallets (config["wallets"][i]["name"])
    - chain                   : chain normalisée ("ethereum", "solana", "base", ...)
    - amount_usd              : montant notionnel
    - reason                  : ex "daily_profit_sweep"
    - meta                    : infos additionnelles (pnl, ratio, etc.)
    """
    from_wallet: str
    to_wallet: str
    chain: str
    amount_usd: float
    reason: str
    meta: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# WalletFlowManager
# ============================================================================


class WalletFlowManager:
    """
    Gère la logique de "finance interne" entre wallets, par ex :

    - sweeps des profits journaliers des wallets de trading vers les wallets "profits"
      ou "vault" définis dans config["wallet_roles"].
    - (extensions possibles : top-up fees, emergency buffer, etc.)

    Cette v1 ne fait que produire des TransferPlan. L'exécution réelle (on-chain)
    reste à la charge d'un module séparé / ExecutionEngine spécial.
    """

    def __init__(self, wallet_manager: WalletManager, raw_config: Optional[Dict[str, Any]] = None) -> None:
        self._wm = wallet_manager
        flows_cfg = (raw_config or {}).get("wallet_flows", {}) or {}

        # Seuil de profits journaliers à partir duquel on déclenche un sweep
        self.min_profit_to_sweep_usd: float = float(
            flows_cfg.get("min_profit_to_sweep_usd", 100.0)
        )
        # Fraction des profits à déplacer (0.5 => 50%)
        self.sweep_fraction: float = float(
            flows_cfg.get("sweep_fraction", 0.5)
        )

        logger.info(
            "[WalletFlowManager] Initialisé (min_profit_to_sweep_usd=%.2f, sweep_fraction=%.2f)",
            self.min_profit_to_sweep_usd,
            self.sweep_fraction,
        )

    # ----------------------------------------------------------------------
    # Sweeps de profits journaliers
    # ----------------------------------------------------------------------
    def plan_daily_profit_sweeps(self) -> List[TransferPlan]:
        """
        Génère des plans de transferts depuis les wallets de trading (MAIN/SCALPING/COPY/SWING/TEST)
        vers les wallets "profits" ou "vault" définis dans config["wallet_roles"].

        Logique simplifiée :

        Pour chaque wallet W :
          - si risk.enabled = False -> skip
          - si role ∈ {MAIN, SCALPING, COPYTRADING, SWING, TEST}
          - si daily_pnl_usd(W) > min_profit_to_sweep_usd
             amount = daily_pnl_usd(W) * sweep_fraction
             profit_wallet = get_wallet_for_chain(chain, "profits")
             si pas trouvé -> fallback get_wallet_for_chain(chain, "vault")
             si trouvé et différent de W -> TransferPlan(W -> profit_wallet, amount)
        """
        plans: List[TransferPlan] = []

        for name, cfg in self._wm._wallets_config.items():
            state = self._wm.get_wallet_state(name)
            if not state:
                continue

            if not cfg.risk.enabled:
                continue

            # On ne sweep que certains rôles (trading-ish)
            if cfg.role not in (
                WalletRole.MAIN,
                WalletRole.SCALPING,
                WalletRole.COPYTRADING,
                WalletRole.SWING,
                WalletRole.TEST,
            ):
                continue

            pnl = float(state.daily_pnl_usd)
            if pnl <= self.min_profit_to_sweep_usd:
                continue

            sweep_amount = pnl * self.sweep_fraction
            if sweep_amount <= 0:
                continue

            # Trouver wallet de profits/vault pour la même chain
            chain_norm = WalletManager._normalize_chain(cfg.chain)
            profit_wallet = self._wm.get_wallet_for_chain(chain_norm, purpose="profits")
            if not profit_wallet or profit_wallet == name:
                profit_wallet = self._wm.get_wallet_for_chain(chain_norm, purpose="vault")

            if not profit_wallet or profit_wallet == name:
                logger.info(
                    "[WalletFlowManager] Aucun wallet 'profits/vault' trouvé pour chain=%s (from=%s) — skip",
                    chain_norm,
                    name,
                )
                continue

            plan = TransferPlan(
                from_wallet=name,
                to_wallet=profit_wallet,
                chain=chain_norm,
                amount_usd=sweep_amount,
                reason="daily_profit_sweep",
                meta={
                    "daily_pnl_usd": pnl,
                    "sweep_fraction": self.sweep_fraction,
                },
            )
            plans.append(plan)

            logger.info(
                "[WalletFlowManager] Plan daily_profit_sweep: %s -> %s (chain=%s, amount=%.2f)",
                plan.from_wallet,
                plan.to_wallet,
                plan.chain,
                plan.amount_usd,
            )

        return plans
