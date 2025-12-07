from typing import Any, Dict, List

from bot.chains.base_chain import BaseChain


class EvmIndexerClient:
    def __init__(self, chain: BaseChain) -> None:
        self.chain = chain

    async def fetch_latest_block(self) -> int:
        return await self.chain.fetch_latest_block_number()

    async def fetch_events_range(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        return await self.chain.fetch_new_events(from_block, to_block)
