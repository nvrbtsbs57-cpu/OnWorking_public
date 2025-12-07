from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class NormalizedEvent:
    chain: str
    block: int
    kind: str
    score: float
    raw: Dict[str, Any]
