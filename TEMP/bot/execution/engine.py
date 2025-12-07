# bot/execution/engine.py

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, List

from bot.wallets.manager import WalletManager, WalletRole


logger = logging.getLogger(__name__)


# ============================================================================
# Enums & Dataclasses
# ============================================================================


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ExecutionMode(str, Enum):
    """
    Mode d'exécution du moteur bas niveau.

    - DRY_RUN : on construit "logiquement" la TX, mais on n'envoie rien.
                C'est le mode par défaut pour M10/M11.
    - LIVE    : on enverra réellement la TX (implémentation future dans
                _build_and_send_tx_on_chain).
    """
    DRY_RUN = "DRY_RUN"
    LIVE = "LIVE"


@dataclass
class ExecutionRequest:
    """
    Représente une intention de trade / swap.

    IMPORTANT: ceci ne contient pas encore la transaction brute, c'est une
    abstraction indépendante du type de chain (EVM / Solana / BSC...).
    """
    chain: str                  # "ethereum", "arbitrum", "base", "solana", "bsc", ...
    symbol_in: str              # token envoyé (ex: "USDC")
    symbol_out: str             # token reçu (ex: "WETH")
    amount_in: float            # quantité de token_in à vendre
    side: OrderSide             # BUY ou SELL (sémantique stratégie)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None      # si LIMIT (prix symbol_out/symbol_in)
    notional_usd: Optional[float] = None     # pour risk mgmt & logs
    strategy_tag: str = "generic"            # ex: "whale-follow", "liquidity-grab"
    prefer_role: Optional[WalletRole] = None
    require_tags: Optional[List[str]] = None
    extra: Dict[str, Any] = None             # slippage, pool, route, etc.


@dataclass
class ExecutionResult:
    """
    Résultat d'une tentative d'exécution.
    """
    success: bool
    tx_hash: Optional[str] = None
    reason: Optional[str] = None
    used_wallet: Optional[str] = None
    extra: Dict[str, Any] = None


# ============================================================================
# ExecutionEngine
# ============================================================================


class ExecutionEngine:
    """
    Orchestrateur d'exécution cross-chain.

    - Choisit le wallet via WalletManager
    - Récupère le client RPC adapté : rpc_clients[chain]
    - Construit + envoie la transaction via `_build_and_send_tx_on_chain`.

    IMPORTANT:
        Par défaut, ce moteur est en mode DRY_RUN : aucune vraie transaction
        n'est envoyée sur la chain, même si `_build_and_send_tx_on_chain`
        était implémentée. Pour passer en LIVE, il faudra construire
        explicitement l'engine avec mode=ExecutionMode.LIVE.
    """

    def __init__(
        self,
        rpc_clients: Dict[str, Any],
        wallet_manager: WalletManager,
        *,
        mode: ExecutionMode | str = ExecutionMode.DRY_RUN,
    ) -> None:
        """
        rpc_clients: dict chain -> client (web3, ou wrapper interne)
        wallet_manager: instance de WalletManager initialisée depuis config
        mode: DRY_RUN (par défaut) ou LIVE
        """
        self._rpc_clients = rpc_clients
        self._wallet_manager = wallet_manager

        if isinstance(mode, str):
            mode = ExecutionMode(mode.upper())
        self._mode: ExecutionMode = mode

        logger.info(
            "[ExecutionEngine] Initialisé avec mode=%s (chains=%s)",
            self._mode.value,
            list(self._rpc_clients.keys()),
        )

    # ------------------------------------------------------------------
    # API principale
    # ------------------------------------------------------------------
    def execute(self, req: ExecutionRequest) -> ExecutionResult:
        """
        Exécute un ordre logique (ExecutionRequest) en :
        1) Sélectionnant un wallet compatible
        2) Construisant la TX (stub pour l'instant)
        3) L'envoyant sur la chain correspondante (ou pas, en DRY_RUN)

        Retourne un ExecutionResult (succès / échec).
        """
        logger.info(
            "[ExecutionEngine] Nouvelle requête: mode=%s chain=%s %s %s->%s "
            "amount_in=%.6f strategy=%s",
            self._mode.value,
            req.chain,
            req.side.value,
            req.symbol_in,
            req.symbol_out,
            req.amount_in,
            req.strategy_tag,
        )

        # notional_usd : fallback simple si pas fourni (utile pour le risk)
        if req.notional_usd is None:
            # Si tu as un pricer global, c'est ici qu'on l'appellera plus tard.
            req.notional_usd = req.amount_in

        # 1) Choix du wallet
        wallet_name = self._wallet_manager.choose_wallet_for_trade(
            chain=req.chain,
            strategy_tag=req.strategy_tag,
            notional_usd=req.notional_usd or 0.0,
            prefer_role=req.prefer_role,
            require_tags=req.require_tags or [],
        )

        if not wallet_name:
            return ExecutionResult(
                success=False,
                reason="Aucun wallet valide pour cette exécution",
            )

        # NOTE:
        #   En DRY_RUN, on n'a pas strictement besoin de la clé privée.
        #   On tolère son absence, mais on log un warning pour aider à
        #   préparer la phase LIVE.
        pk: Optional[str] = self._wallet_manager.get_private_key(wallet_name)
        if pk is None:
            msg = f"Clé privée introuvable pour wallet {wallet_name}"
            if self._mode is ExecutionMode.LIVE:
                # En mode LIVE, c'est bloquant.
                return ExecutionResult(
                    success=False,
                    reason=msg,
                    used_wallet=wallet_name,
                )
            else:
                # En DRY_RUN, on continue mais on log clairement.
                logger.warning(
                    "[ExecutionEngine] %s (mode=%s, on continue en DRY_RUN).",
                    msg,
                    self._mode.value,
                )

        client = self._rpc_clients.get(req.chain)
        if not client:
            logger.error(
                "[ExecutionEngine] Aucun RPC client pour chain=%s (config chains / rpc_clients à compléter)",
                req.chain,
            )
            return ExecutionResult(
                success=False,
                reason=f"RPC client introuvable pour chain {req.chain}",
                used_wallet=wallet_name,
            )

        # 2) Construction + envoi (ou simulation) de la TX
        try:
            tx_hash, extra = self._build_and_send_tx_on_chain(
                client=client,
                private_key=pk,
                wallet_name=wallet_name,
                req=req,
            )
        except Exception as e:
            logger.exception("[ExecutionEngine] Erreur lors de l'envoi de la TX: %s", e)
            return ExecutionResult(
                success=False,
                reason=str(e),
                used_wallet=wallet_name,
            )

        logger.info(
            "[ExecutionEngine] TX %s 'envoyée' (mode=%s): chain=%s wallet=%s tx=%s",
            "simulée" if self._mode is ExecutionMode.DRY_RUN else "envoyée",
            self._mode.value,
            req.chain,
            wallet_name,
            tx_hash,
        )

        # 3) Retour ExecutionResult
        extra = extra or {}
        extra.setdefault("execution_mode", self._mode.value)

        return ExecutionResult(
            success=True,
            tx_hash=tx_hash,
            used_wallet=wallet_name,
            extra=extra,
        )

    # ------------------------------------------------------------------
    # À implémenter : logique DEX / Web3 concrète
    # ------------------------------------------------------------------
    def _build_and_send_tx_on_chain(
        self,
        *,
        client: Any,
        private_key: Optional[str],
        wallet_name: str,
        req: ExecutionRequest,
    ) -> tuple[str, Dict[str, Any]]:
        """
        C'est ici que tu brancheras ta vraie logique d'exécution :

        - Pour EVM (ethereum / arbitrum / base / bsc):
            - construction d'un swap (Uniswap, 1inch, Router maison, etc.)
            - estimation du gas, slippage, route
            - signature avec la private_key
            - envoi via client (web3.py ou ton wrapper interne)

        - Pour Solana:
            - construction d'une instruction swap (Jupiter, Raydium, Orca...)
            - création et signature de la Transaction
            - envoi via RPC Solana

        MODE DRY_RUN (par défaut):
            On ne DOIT PAS envoyer de vraie transaction. On construit au mieux
            un "fake tx" (tx_hash factice) et on enrichit extra pour debug.

        MODE LIVE (plus tard):
            Ce sera ici que tu brancheras réellement l'envoi de transaction.

        Retourne:
            (tx_hash: str, extra: dict)
        """
        logger.warning(
            "[ExecutionEngine] _build_and_send_tx_on_chain est encore en mode STUB "
            "(aucune vraie transaction envoyée) [mode=%s].",
            self._mode.value,
        )
        # FAKE TX pour debug / test pipeline
        fake_tx_hash = "0x" + "deadbeef" * 8
        extra = {
            "stub": True,
            "wallet": wallet_name,
            "chain": req.chain,
            "symbol_in": req.symbol_in,
            "symbol_out": req.symbol_out,
            "amount_in": req.amount_in,
            "order_type": req.order_type.value,
            "side": req.side.value,
        }
        return fake_tx_hash, extra

