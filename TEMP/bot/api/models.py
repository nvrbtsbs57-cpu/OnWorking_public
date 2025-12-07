from __future__ import annotations

from pydantic import BaseModel
from typing import List, Optional


class HealthResponse(BaseModel):
    status: str
    indexer_running: bool
    normalizer_running: bool
    mapper_running: bool
    agent_running: bool
    alerting_running: bool


class BlockResponse(BaseModel):
    chain: str
    block_number: int
    timestamp: float
    tx_count: int


class TickResponse(BaseModel):
    chain: str
    block_number: int
    activity_level: float
    volume_estimate: float
    price_impact_estimate: float


class EventResponse(BaseModel):
    type: str
    chain: str
    block_number: int
    severity: str


class SignalResponse(BaseModel):
    type: str
    confidence: float
    reason: str
