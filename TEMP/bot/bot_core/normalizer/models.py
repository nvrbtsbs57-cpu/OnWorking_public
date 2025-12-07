from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class NormalizedTick:
    """
    Représente un “tick” normalisé venant d’un bloc.
    Utilisé par l’agent pour générer des signaux.
    """
    chain: str
    block_number: int
    timestamp: float
    tx_count: int
    activity_level: float  # TXs normalisés
    volume_estimate: float  # estimation simplifiée (pas de volume on-chain natif)
    price_impact_estimate: float  # variation estimée
