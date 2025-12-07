from __future__ import annotations

from typing import List

from bot.bot_core.normalizer.normalizer_engine import NormalizerEngine
from bot.bot_core.normalizer.patterns import NormalizedEvent


class QueryEngine:
    """
    Facade entre le NormalizerEngine et l'API HTTP.

    Pour l'instant :
      - expose simplement les derniers événements normalisés
      - l'AgentEngine les récupère via l'endpoint /events
    """

    def __init__(self, normalizer: NormalizerEngine) -> None:
        self.normalizer = normalizer

    def get_recent_events(self) -> List[NormalizedEvent]:
        """
        Retourne les derniers événements normalisés.

        NormalizerEngine.get_recent_events() renvoie déjà une liste
        de NormalizedEvent, donc on ne fait que passer la valeur.
        """
        return self.normalizer.get_recent_events()
