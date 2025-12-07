from collections import deque
from typing import Deque, List

from .patterns import NormalizedEvent


class EventAggregator:
    def __init__(self, max_size: int = 200) -> None:
        self._events: Deque[NormalizedEvent] = deque(maxlen=max_size)

    def add(self, event: NormalizedEvent) -> None:
        self._events.append(event)

    def get_all(self) -> List[NormalizedEvent]:
        return list(self._events)
