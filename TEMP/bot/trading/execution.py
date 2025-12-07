# file: bot/trading/execution.py

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from bot.core.logging import get_logger

logger = get_logger(__name__)


class ExecutionEngine:
    """
    Adapter haut-niveau autour du moteur interne d'exécution (PaperTrader, etc.).

    Rôle :
    - délègue l'exécution à un moteur interne (`inner_engine`),
    - récupère le `wallet_id` depuis `signal.meta["wallet_id"]`
      ou `trade.meta["wallet_id"]`,
    - extrait le PnL et les fees simulés du trade,
    - propage PnL + fees vers le `RuntimeWalletManager`,
    - renvoie l'objet `trade` retourné par le moteur interne.

    Ce moteur est conçu pour le mode PAPER / DRY_RUN :
    toute la logique de "vraie" exécution on-chain doit vivre
    dans un autre moteur, non branché ici.
    """

    def __init__(
        self,
        inner_engine: Any,
        *,
        wallet_manager: Optional[Any] = None,
    ) -> None:
        """
        Parameters
        ----------
        inner_engine:
            Moteur d'exécution concret, typiquement `PaperTrader` ou
            un moteur compatible avec `execute_signal(signal, prices=None)`.

        wallet_manager:
            Instance de `RuntimeWalletManager` (ou équivalent) qui expose
            une méthode :
                on_trade_closed(wallet_id, realized_pnl_usd, fees_paid_usd=0)
            Si None, aucun PnL ne sera propagé.
        """
        self.inner_engine = inner_engine
        self.wallet_manager = wallet_manager

    # ------------------------------------------------------------------ #
    # API principale
    # ------------------------------------------------------------------ #
    def execute_signal(self, signal: Any, prices: Optional[dict] = None) -> Any:
        """
        Exécute un signal de trading via le moteur interne et met à jour
        les wallets runtime si un `wallet_id` est disponible.

        Parameters
        ----------
        signal:
            Objet signal spécifique à la stratégie.
            Peut contenir `meta = {"wallet_id": "sniper_sol", ...}`.

        prices:
            Mapping optionnel (ex: prix spot, best bid/ask, etc.) transmis
            tel quel au moteur interne.

        Returns
        -------
        trade:
            Objet trade renvoyé par le moteur interne.
        """
        # 1) Exécution via le moteur interne (PaperTrader, etc.)
        trade = self.inner_engine.execute_signal(signal, prices=prices)

        # 2) Récupération du wallet_id depuis le signal ou le trade
        wallet_id: Optional[str] = None

        meta_signal = getattr(signal, "meta", None)
        if isinstance(meta_signal, dict):
            wallet_id = meta_signal.get("wallet_id")

        if wallet_id is None:
            meta_trade = getattr(trade, "meta", None)
            if isinstance(meta_trade, dict):
                wallet_id = meta_trade.get("wallet_id")

        # 3) Extraction du PnL simulé et des fees à partir du trade
        pnl_usd = Decimal("0")
        fees_usd = Decimal("0")

        meta_trade = getattr(trade, "meta", None)
        if isinstance(meta_trade, dict):
            # On essaie différents champs possibles pour le PnL simulé
            raw_pnl = (
                meta_trade.get("pnl_sim_usd")
                or meta_trade.get("last_trade_pnl_usd")
                or meta_trade.get("pnl_usd")
            )
            if raw_pnl is not None:
                try:
                    pnl_usd = Decimal(str(raw_pnl))
                except Exception:
                    logger.exception(
                        "ExecutionEngine.execute_signal: impossible de parser pnl_usd=%r",
                        raw_pnl,
                    )

        # Fees : on privilégie l'attribut `trade.fee` si présent
        fee_attr = getattr(trade, "fee", None)
        if fee_attr is not None:
            try:
                fees_usd = Decimal(str(fee_attr))
            except Exception:
                logger.exception(
                    "ExecutionEngine.execute_signal: impossible de parser fee=%r",
                    fee_attr,
                )
        else:
            # Fallback éventuel sur meta["fees_sim_usd"]
            if isinstance(meta_trade, dict):
                raw_fee = meta_trade.get("fees_sim_usd")
                if raw_fee is not None:
                    try:
                        fees_usd = Decimal(str(raw_fee))
                    except Exception:
                        logger.exception(
                            "ExecutionEngine.execute_signal: "
                            "impossible de parser fees_sim_usd=%r",
                            raw_fee,
                        )

        # 4) Propagation vers RuntimeWalletManager
        if wallet_id is not None and hasattr(self.wallet_manager, "on_trade_closed"):
            try:
                # Signature attendue dans RuntimeWalletManager :
                # on_trade_closed(wallet_id, realized_pnl_usd, fees_paid_usd=0)
                self.wallet_manager.on_trade_closed(wallet_id, pnl_usd, fees_usd)
            except Exception:
                logger.exception(
                    "Erreur lors de la propagation PnL vers RuntimeWalletManager "
                    "(wallet_id=%s, pnl_usd=%s, fees_usd=%s)",
                    wallet_id,
                    str(pnl_usd),
                    str(fees_usd),
                )
        else:
            logger.debug(
                "ExecutionEngine.execute_signal: aucun wallet_id ou pas de "
                "wallet_manager.on_trade_closed"
            )

        return trade


__all__ = ["ExecutionEngine"]

