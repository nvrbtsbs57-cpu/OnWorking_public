from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RunMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class SafetyMode(str, Enum):
    SAFE = "safe"         # très prudent
    NORMAL = "normal"     # par défaut
    DEGEN = "degen"       # pour jouer en GODMODE 😈


class BotProfile(str, Enum):
    GODMODE = "GODMODE"
    PRO = "PRO"


@dataclass
class ModeConfig:
    """
    Configuration unifiée des modes du bot.
    """
    profile: BotProfile = BotProfile.GODMODE
    run_mode: RunMode = RunMode.PAPER
    safety_mode: SafetyMode = SafetyMode.NORMAL

    @classmethod
    def from_dict(cls, raw: dict) -> "ModeConfig":
        """
        Construit un ModeConfig à partir du dict de config global (config.json déjà chargé).
        Les clés sont optionnelles, on a des valeurs par défaut.
        """
        profile_str = str(raw.get("BOT_MODE", "GODMODE")).upper()
        run_mode_str = str(raw.get("RUN_MODE", "paper")).lower()
        safety_str = str(raw.get("SAFETY_MODE", "normal")).lower()

        # mapping robust => ne lève pas d'erreur si la config est bizarre
        profile = BotProfile.GODMODE
        if profile_str in ("GODMODE", "PRO"):
            profile = BotProfile(profile_str)

        run_mode = RunMode.PAPER
        if run_mode_str in ("paper", "live"):
            run_mode = RunMode(run_mode_str)

        safety_mode = SafetyMode.NORMAL
        if safety_str in ("safe", "normal", "degen"):
            safety_mode = SafetyMode(safety_str)

        return cls(
            profile=profile,
            run_mode=run_mode,
            safety_mode=safety_mode,
        )
