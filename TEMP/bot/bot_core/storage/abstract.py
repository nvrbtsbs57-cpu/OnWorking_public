from abc import ABC, abstractmethod
from typing import Any, Dict, List


class AbstractStorage(ABC):
    @abstractmethod
    def save_events(self, chain: str, events: List[Dict[str, Any]]) -> None:
        ...

    @abstractmethod
    def get_last_block(self, chain: str) -> int:
        ...

    @abstractmethod
    def set_last_block(self, chain: str, block: int) -> None:
        ...
