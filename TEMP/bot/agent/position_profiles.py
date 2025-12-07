from __future__ import annotations

from decimal import Decimal

from bot.agent.modes import SafetyMode
from bot.trading.positions import PositionConfig


def build_position_config_for_safety_mode(mode: SafetyMode) -> PositionConfig:
    """
    Map simple : SafetyMode (SAFE / NORMAL / DEGEN)
    -> PositionConfig (TP1/TP2/SL/Trailing).

    Pour l'instant :
      - SAFE   : très cashflow, beaucoup de sécurisation, petit runner
      - NORMAL : ton profil 40/40/20 "équilibré"
      - DEGEN  : plus de runner, TP plus loin

    On pourra affiner plus tard ou faire du per-trade DEGEN
    sur les grosses baleines, mais pour l'instant c'est global.
    """
    cfg = PositionConfig()

    if mode == SafetyMode.SAFE:
        # Profil "salaire", sécurise vite et fort
        cfg.take_profit.tp1_pct = Decimal("0.15")        # +15%
        cfg.take_profit.tp1_size_pct = Decimal("0.6")    # 60% de la position
        cfg.take_profit.tp2_pct = Decimal("0.30")        # +30%
        cfg.take_profit.tp2_size_pct = Decimal("0.3")    # 30%
        # runner implicite = 10%

        cfg.stop.sl_pct = Decimal("0.08")                # -8%
        cfg.stop.trailing_activation_pct = Decimal("0.25")  # trailing dès +25%
        cfg.stop.trailing_pct = Decimal("0.15")          # stop 15% sous le plus haut

    elif mode == SafetyMode.DEGEN:
        # Profil "mode fou" manuel (pas encore auto-baleine)
        cfg.take_profit.tp1_pct = Decimal("0.20")        # +20%
        cfg.take_profit.tp1_size_pct = Decimal("0.3")    # 30%
        cfg.take_profit.tp2_pct = Decimal("0.50")        # +50%
        cfg.take_profit.tp2_size_pct = Decimal("0.3")    # 30%
        # runner = 40%

        cfg.stop.sl_pct = Decimal("0.10")                # -10%
        cfg.stop.trailing_activation_pct = Decimal("0.30")
        cfg.stop.trailing_pct = Decimal("0.20")          # trailing plus large

    else:
        # NORMAL = ton 40/40/20 pour memecoin farm 24/24
        cfg.take_profit.tp1_pct = Decimal("0.20")        # +20%
        cfg.take_profit.tp1_size_pct = Decimal("0.4")    # 40%
        cfg.take_profit.tp2_pct = Decimal("0.40")        # +40%
        cfg.take_profit.tp2_size_pct = Decimal("0.4")    # 40%
        # runner = 20%

        cfg.stop.sl_pct = Decimal("0.10")                # -10%
        cfg.stop.trailing_activation_pct = Decimal("0.30")
        cfg.stop.trailing_pct = Decimal("0.15")

    return cfg
