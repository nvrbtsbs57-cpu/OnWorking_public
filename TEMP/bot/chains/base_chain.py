from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ChainConfig:
    name: str
    type: str
    rpc_url: str
    chain_id: int
    enabled: bool = True
    log_addresses: Optional[List[str]] = None


class BaseChain:
    def __init__(self, config: ChainConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    async def fetch_latest_block_number(self) -> int:
        raise NotImplementedError

    async def fetch_new_events(self, from_block: int, to_block: int) -> List[Dict[str, Any]]:
        raise NotImplementedError
