from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BaseEvent:
    chain: str
    block_number: int
    timestamp: float
    type: str


@dataclass
class ActivitySpikeEvent(BaseEvent):
    tx_count: int
    activity_level: float
    severity: str


@dataclass
class VolumeSpikeEvent(BaseEvent):
    volume_estimate: float
    severity: str


@dataclass
class ImpactSpikeEvent(BaseEvent):
    impact_value: float
    severity: str
