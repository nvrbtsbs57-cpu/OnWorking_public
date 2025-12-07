from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Any, Optional

from bot.bot_core.indexer.rpc_client import RPCClient
from bot.bot_core.indexer.token_metadata import TokenMetadataProvider

logger = logging.getLogger(__name__)


# =====================================================================
# Dataclasses
# =====================================================================

@dataclass
class WhaleTransfer:
    block: int
    tx_hash: str
    sender: str
    receiver: str
    token: str           # token address
    amount: float        # token amount (décimales déjà appliquées)
    usd_value: float
    direction: str       # 'inflow' or 'outflow'


@dataclass
class BlockWhaleActivity:
    block: int
    transfers: List[WhaleTransfer]
    total_usd: float
    whale_count: int


# =====================================================================
# Whale Scanner FULL PRO
# =====================================================================

class WhaleScanner:
    """
    GODMODE Whale Scanner — FULL PRO

    - Scanne les logs sur un range de blocs
    - Extrait les gros transferts ("whales")
    - Calcule la valeur USD si possible
    - Log structuré pour AlertEngine (via logging bridge)
    """

    MIN_WHALE_USD = 50_000  # valeur par défaut

    def __init__(
        self,
        rpc: RPCClient,
        token_meta: TokenMetadataProvider,
        chain: str,
        min_whale_usd: Optional[float] = None,
    ):
        self.rpc = rpc
        self.token_meta = token_meta
        self.chain = chain
        self.min_whale_usd = float(min_whale_usd) if min_whale_usd is not None else float(
            self.MIN_WHALE_USD
        )

    # --------------------------------------------------------------
    async def scan_block_range(self, start: int, end: int) -> BlockWhaleActivity:
        """
        Scan asynchrone d'un range de blocs.
        Retourne uniquement les transferts "whale".
        Ne lève pas d'exception : en cas d'erreur, log + résultat vide.
        """

        try:
            logs = await self.rpc.get_logs(start, end)
        except Exception as e:
            logger.error(
                "WhaleScanner: erreur lors de get_logs(chain=%s, start=%d, end=%d): %s",
                self.chain,
                start,
                end,
                e,
                extra={
                    "event": "whale_scan_error",
                    "chain": self.chain,
                    "start_block": start,
                    "end_block": end,
                    "error": str(e),
                },
            )
            return BlockWhaleActivity(
                block=end,
                transfers=[],
                total_usd=0.0,
                whale_count=0,
            )

        if not logs:
            logger.debug(
                "WhaleScanner: aucun log sur chain=%s range=%d-%d",
                self.chain,
                start,
                end,
                extra={
                    "event": "whale_scan_empty",
                    "chain": self.chain,
                    "start_block": start,
                    "end_block": end,
                },
            )
            return BlockWhaleActivity(
                block=end,
                transfers=[],
                total_usd=0.0,
                whale_count=0,
            )

        whale_transfers: List[WhaleTransfer] = []

        for log in logs:
            parsed = self._parse_transfer(log)
            if parsed is None:
                continue

            if parsed.usd_value >= self.min_whale_usd:
                whale_transfers.append(parsed)

                # Log WARNING structuré pour AlertEngine (une ligne par whale)
                try:
                    symbol = self.token_meta.get_token_symbol(self.chain, parsed.token)
                except Exception:
                    symbol = None

                logger.warning(
                    "Whale détectée sur %s: %.0f USD (%s) tx=%s",
                    self.chain,
                    parsed.usd_value,
                    symbol or parsed.token,
                    parsed.tx_hash,
                    extra={
                        "event": "whale_tx",
                        "chain": self.chain,
                        "block": parsed.block,
                        "tx_hash": parsed.tx_hash,
                        "sender": parsed.sender,
                        "receiver": parsed.receiver,
                        "token": parsed.token,
                        "token_symbol": symbol,
                        "amount": parsed.amount,
                        "usd_value": parsed.usd_value,
                        "direction": parsed.direction,
                        "threshold_usd": self.min_whale_usd,
                    },
                )

        total_usd = sum(t.usd_value for t in whale_transfers)

        result = BlockWhaleActivity(
            block=end,
            transfers=whale_transfers,
            total_usd=total_usd,
            whale_count=len(whale_transfers),
        )

        # Log de résumé (INFO) pour la télémétrie / debug
        logger.info(
            "WhaleScanner: chain=%s range=%d-%d whales=%d total_usd=%.0f",
            self.chain,
            start,
            end,
            len(whale_transfers),
            total_usd,
            extra={
                "event": "whale_scan_summary",
                "chain": self.chain,
                "start_block": start,
                "end_block": end,
                "whales": len(whale_transfers),
                "total_usd": total_usd,
                "threshold_usd": self.min_whale_usd,
            },
        )

        return result

    # --------------------------------------------------------------
    def _parse_transfer(self, log: Dict[str, Any]) -> WhaleTransfer | None:
        """
        Essaie de parser un event de transfert.
        La logique de metadata (decimals / price) est robuste aux erreurs.
        """

        # Pas un event de type "Transfer" ?
        if "Transfer" not in str(log.get("event", "")):
            return None

        amount_raw = log.get("amount")
        token_addr = log.get("address")
        sender = log.get("from")
        receiver = log.get("to")
        block = log.get("block_number")
        tx = log.get("transaction_hash")

        if None in (amount_raw, token_addr, sender, receiver, block, tx):
            return None

        # Récupération des décimales + prix avec protection d'erreurs
        decimals: Optional[int]
        price_usd: Optional[float]

        try:
            decimals = self.token_meta.get_token_decimals(self.chain, token_addr)
        except Exception as e:
            logger.debug(
                "WhaleScanner: impossible de récupérer decimals pour %s sur %s: %s",
                token_addr,
                self.chain,
                e,
                extra={
                    "event": "whale_meta_decimals_error",
                    "chain": self.chain,
                    "token": token_addr,
                    "error": str(e),
                },
            )
            decimals = None

        try:
            price_usd = self.token_meta.get_token_price_usd(self.chain, token_addr)
        except Exception as e:
            logger.debug(
                "WhaleScanner: impossible de récupérer le prix USD pour %s sur %s: %s",
                token_addr,
                self.chain,
                e,
                extra={
                    "event": "whale_meta_price_error",
                    "chain": self.chain,
                    "token": token_addr,
                    "error": str(e),
                },
            )
            price_usd = None

        # Calcul du montant token / USD
        try:
            if decimals is not None and decimals > 0:
                amount = float(amount_raw) / (10 ** decimals)
            else:
                amount = float(amount_raw)
        except Exception:
            amount = float(amount_raw) if not isinstance(amount_raw, (int, float)) else float(amount_raw)

        usd_value = amount * float(price_usd) if price_usd else 0.0

        direction = "inflow" if self._is_inflow(receiver) else "outflow"

        return WhaleTransfer(
            block=int(block),
            tx_hash=str(tx),
            sender=str(sender),
            receiver=str(receiver),
            token=str(token_addr),
            amount=amount,
            usd_value=usd_value,
            direction=direction,
        )

    # --------------------------------------------------------------
    @staticmethod
    def _is_inflow(receiver: str) -> bool:
        """
        Heuristique placeholder:
        inflow = EOA (non null-address)
        outflow = smart contract / adresse spéciale.
        """
        if not isinstance(receiver, str):
            return False
        return not receiver.lower().startswith("0x0000")
