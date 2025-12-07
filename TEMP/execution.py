from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from bot.core.logging import get_logger

logger = get_logger(__name__)


class ExecutionEngine:
    """
    Wrapper haut-niveau autour d'un moteur d'exécution interne (PaperTrader ou autre).

    Rôle actuel (M10 / PAPER_ONCHAIN) :
    - déléguer l'exécution réelle au moteur interne (`inner_engine`),
    - récupérer éventuellement `wallet_id` depuis `signal.meta["wallet_id"]`
      ou `trade.meta["wallet_id"]`,
    - propager un PnL simulé (pour l'instant 0 USD) vers RuntimeWalletManager
      via `wallet_manager.on_trade_closed(wallet_id, pnl_usd)`,
    - renvoyer le trade (comme `inner_engine.execute_signal`).
    """

    def __init__(self, inner_engine: Any, wallet_manager: Optional[Any] = None) -> None:
        """
        :param inner_engine: moteur réel d'exécution (ex: PaperTrader)
                             doit exposer `execute_signal(signal, prices=None) -> Trade`.
        :param wallet_manager: instance optionnelle de RuntimeWalletManager
                               (ou objet compatible avec `on_trade_closed(wallet_id, pnl_usd)`).
        """
        self.inner_engine = inner_engine
        self.wallet_manager = wallet_manager

    # ------------------------------------------------------------------
    # Helpers de wiring
    # ------------------------------------------------------------------
    def set_wallet_manager(self, wallet_manager: Optional[Any]) -> None:
        """
        Permet d'injecter ou de changer le RuntimeWalletManager à chaud.
        """
        self.wallet_manager = wallet_manager

    # ------------------------------------------------------------------
    # Exécution d'un signal
    # ------------------------------------------------------------------
    def execute_signal(self, signal, prices=None):
        """
        Adapter haut-niveau autour du moteur interne.

        - délègue l'exécution au moteur interne (`inner_engine`),
        - récupère le wallet_id depuis `signal.meta["wallet_id"]` ou `trade.meta["wallet_id"]`,
        - propage un `pnl_usd` (pour l'instant 0) vers RuntimeWalletManager,
        - renvoie le trade (comme `inner_engine.execute_signal`).
        """
        # 1) Exécution réelle via le moteur interne
        trade = self.inner_engine.execute_signal(signal, prices=prices)

        # 2) Récupération du wallet_id depuis le signal ou le trade
        wallet_id = None

        meta = getattr(signal, "meta", None)
        if isinstance(meta, dict):
            wallet_id = meta.get("wallet_id")

        if wallet_id is None:
            meta_trade = getattr(trade, "meta", None)
            if isinstance(meta_trade, dict):
                wallet_id = meta_trade.get("wallet_id")

        # 3) PnL simulé — pour l’instant on ne se prend pas la tête
        pnl_usd = Decimal("0")

        # 4) Propagation vers RuntimeWalletManager
        if wallet_id is not None and getattr(self.wallet_manager, "on_trade_closed", None):
            try:
                self.wallet_manager.on_trade_closed(wallet_id, pnl_usd)
            except Exception:
                logger.exception(
                    "Erreur lors de la propagation PnL vers RuntimeWalletManager "
                    "(wallet_id=%s, pnl_usd=%s)",
                    wallet_id,
                    str(pnl_usd),
                )
        else:
            logger.debug(
                "ExecutionEngine.execute_signal: aucun wallet_id ou pas de "
                "wallet_manager.on_trade_closed (wallet_id=%r, wallet_manager=%r)",
                wallet_id,
                self.wallet_manager,
            )

        return trade

