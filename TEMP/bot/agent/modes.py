from __future__ import annotations

"""
Pont entre bot.agent et bot.core pour les modes.

On ré-exporte RunMode / SafetyMode / BotProfile / ModeConfig
depuis bot.core.modes pour que l'AgentEngine puisse faire :

    from bot.agent.modes import ModeConfig, SafetyMode
"""

from bot.core.modes import RunMode, SafetyMode, BotProfile, ModeConfig

__all__ = ["RunMode", "SafetyMode", "BotProfile", "ModeConfig"]
