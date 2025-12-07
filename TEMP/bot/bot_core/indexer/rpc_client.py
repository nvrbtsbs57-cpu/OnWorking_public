from __future__ import annotations

import logging
import aiohttp
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =====================================================================================
# RPC CLIENT — Compatible EVM pour l’indexer et le Whale Scanner
# =====================================================================================

class RPCClient:
    """
    Client RPC générique pour les blockchains EVM.
    Supporte :
        - get_latest_block_number()
        - get_logs(start, end)
        - get_block()
        - get_transaction()
    """

    def __init__(self, chain: str, url: str):
        self.chain = chain
        self.url = url

    # ------------------------------------------------------------------
    async def _rpc(self, method: str, params: list[Any]) -> Any:
        """
        Appel JSON-RPC générique.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.url, json=payload) as resp:
                    data = await resp.json()

        except Exception as exc:
            logger.error({
                "module": "RPCClient",
                "chain": self.chain,
                "error": str(exc),
                "method": method,
            })
            raise

        if "error" in data:
            raise RuntimeError(f"RPC ERROR: {data['error']}")

        return data.get("result")

    # ==============================================================================
    # BASICS
    # ==============================================================================

    async def get_latest_block_number(self) -> int:
        """
        Renvoie le numéro du dernier bloc.
        """
        result = await self._rpc("eth_blockNumber", [])
        return int(result, 16)

    async def get_block(self, block_number: int) -> Dict[str, Any]:
        """
        Retourne un bloc complet.
        """
        hex_block = hex(block_number)
        return await self._rpc("eth_getBlockByNumber", [hex_block, True])

    # ==============================================================================
    # LOG SCANNING (utilisé par WhaleScanner)
    # ==============================================================================

    async def get_logs(self, start_block: int, end_block: int) -> List[Dict[str, Any]]:
        """
        Récupère les logs sur une plage de blocs.
        """
        params = [{
            "fromBlock": hex(start_block),
            "toBlock": hex(end_block),
        }]

        result = await self._rpc("eth_getLogs", params)

        logs = []
        for raw in result:
            decoded = self._decode_log(raw)
            if decoded:
                logs.append(decoded)

        return logs

    # ==============================================================================
    # LOG DECODING (minimal)
    # ==============================================================================

    @staticmethod
    def _decode_log(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Décode minimalement les logs Transfer ERC20.
        Structure unifiée compatible avec WhaleScanner.
        """
        # Event signature ERC20 Transfer
        TRANSFER_SIG = "0xddf252ad"

        topics = raw.get("topics", [])
        if not topics:
            return None

        # Check signature
        if not topics[0].startswith(TRANSFER_SIG):
            return None

        try:
            sender = "0x" + topics[1][-40:]
            receiver = "0x" + topics[2][-40:]
            amount_raw = int(raw["data"], 16)

            return {
                "event": "Transfer",
                "address": raw.get("address"),
                "block_number": int(raw.get("blockNumber"), 16),
                "transaction_hash": raw.get("transactionHash"),
                "from": sender,
                "to": receiver,
                "amount": amount_raw,
            }

        except Exception:
            return None
