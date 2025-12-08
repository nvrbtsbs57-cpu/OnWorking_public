#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

# --- Préparation du sys.path pour que "bot" soit importable même lancé en script ---
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# --- Imports projet ---
from bot.core.logging import get_logger  # type: ignore
from bot.core.runtime import ExecutionMode  # type: ignore
from bot.core.tx_guard import TxGuardConfig, can_send_real_tx  # type: ignore

log = get_logger("test_m10_tx_guard_v2")


def main() -> None:
    log.info("=== M10 – test_m10_tx_guard_v2 (TxGuard / M10) ===")

    # Config globale simulée (équivalent d'un bout de config.json)
    raw_cfg = {
        "execution": {
            "tx_guard": {
                # En M10, on veut bloquer TOUT envoi de TX réelle, même si on est en LIVE_150.
                "hard_disable_send_tx": True,
                "allowed_profiles": ["LIVE_150"],
                "log_only": False,
            }
        }
    }

    cfg = TxGuardConfig.from_global_config(raw_cfg)

    # 1) Cas LIVE + profile LIVE_150 -> doit être BLOQUÉ en M10
    ok_live = can_send_real_tx(
        cfg,
        ExecutionMode.LIVE,
        profile="LIVE_150",
        context="unit_test_live_mode",
    )
    log.info(
        "can_send_real_tx(LIVE, profile=LIVE_150) -> %s (attendu: False, M10 bloque tout)",
        ok_live,
    )
    if ok_live:
        log.error(
            "ECHEC: en M10, hard_disable_send_tx=True donc "
            "can_send_real_tx(LIVE, profile=LIVE_150) doit être False."
        )
        raise SystemExit(1)

    # 2) Cas PAPER_ONCHAIN + profile LIVE_150 -> doit aussi être BLOQUÉ (aucune TX réelle)
    ok_paper = can_send_real_tx(
        cfg,
        ExecutionMode.PAPER_ONCHAIN,
        profile="LIVE_150",
        context="unit_test_paper_mode",
    )
    log.info(
        "can_send_real_tx(PAPER_ONCHAIN, profile=LIVE_150) -> %s "
        "(attendu: False, aucune TX réelle en PAPER_ONCHAIN)",
        ok_paper,
    )
    if ok_paper:
        log.error(
            "ECHEC: en PAPER_ONCHAIN, aucune TX réelle ne doit jamais partir, "
            "can_send_real_tx(PAPER_ONCHAIN, profile=LIVE_150) doit être False."
        )
        raise SystemExit(1)

    log.info("OK: garde-fou TxGuard M10 fonctionne comme attendu (tout est bloqué).")
    log.info("test_m10_tx_guard_v2 terminé.")


if __name__ == "__main__":
    main()

