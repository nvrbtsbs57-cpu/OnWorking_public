from __future__ import annotations

import json
import sys
from pathlib import Path
from decimal import Decimal

# ------------------------------------------------------------------
# Bootstrap pour trouver le package "bot" (comme dans start_bot.py)
# ------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.agent.modes import SafetyMode
from bot.agent.position_profiles import build_position_config_for_safety_mode


def load_safety_mode_from_config() -> SafetyMode:
    """
    Lit SAFETY_MODE dans config.json (root du projet).
    Valeurs possibles : safe / normal / degen (case insensitive).
    """
    cfg_path = ROOT_DIR / "config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))

    safety_str = str(
        raw.get("agent", {}).get("SAFETY_MODE", raw.get("SAFETY_MODE", "normal"))
    ).lower()

    if safety_str == "safe":
        return SafetyMode.SAFE
    if safety_str == "degen":
        return SafetyMode.DEGEN
    return SafetyMode.NORMAL


def fmt_pct(x: Decimal) -> str:
    return f"{(float(x) * 100):.1f}%"


def show_profile(mode: SafetyMode) -> None:
    cfg = build_position_config_for_safety_mode(mode)
    print("=" * 60)
    print(f"Profil: {mode.name}")
    print("-" * 60)
    print(
        f"TP1 : {fmt_pct(cfg.take_profit.tp1_pct)}  "
        f"taille={fmt_pct(cfg.take_profit.tp1_size_pct)}"
    )
    print(
        f"TP2 : {fmt_pct(cfg.take_profit.tp2_pct)}  "
        f"taille={fmt_pct(cfg.take_profit.tp2_size_pct)}"
    )
    runner_size = 1.0 - float(
        cfg.take_profit.tp1_size_pct + cfg.take_profit.tp2_size_pct
    )
    print(f"Runner (reste position) ~ {runner_size * 100:.1f}%")
    print()
    print(f"SL  : {fmt_pct(cfg.stop.sl_pct)}")
    print(
        f"Trailing: activation à {fmt_pct(cfg.stop.trailing_activation_pct)} "
        f"/ stop {fmt_pct(cfg.stop.trailing_pct)} sous le plus haut"
    )
    print("=" * 60)
    print()


def main() -> None:
    active_mode = load_safety_mode_from_config()
    print("\n=== SAFETY PROFILES — TP/SL/RUNNER ===\n")
    print(f"Mode actif dans config.json : {active_mode.name}\n")

    for mode in (SafetyMode.SAFE, SafetyMode.NORMAL, SafetyMode.DEGEN):
        show_profile(mode)


if __name__ == "__main__":
    main()
