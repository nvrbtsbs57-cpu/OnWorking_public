from dataclasses import dataclass, field
from typing import List

from bot.normalizer.patterns import NormalizedEvent


@dataclass
class AgentState:
    events: List[NormalizedEvent] = field(default_factory=list)

    def update(self, new_events: List[NormalizedEvent]) -> None:
        self.events = new_events[-200:]
