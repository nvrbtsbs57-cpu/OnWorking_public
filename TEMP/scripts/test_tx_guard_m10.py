#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import sys

# Racine du projet : BOT_GODMODE/BOT_GODMODE
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.core.runtime import ExecutionMode
from bot.core.tx_guard import TxGuardConfig, can_send_real_tx


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Config globale simulée (équivalent d'un config.json chargé)
    raw_cfg = {
        "execution": {
            "tx_guard": {
                "hard_disable_send_tx": True,
                "allowed_profiles": ["LIVE_150"],
                "log_only": False,
            }
        }
    }

    cfg = TxGuardConfig.from_global_config(raw_cfg)

    ok_live = can_send_real_tx(
        cfg,
        ExecutionMode.LIVE,
        profile="LIVE_150",
        context="unit_test_live_mode",
    )

    print(f"can_send_real_tx(LIVE, profile=LIVE_150) -> {ok_live}")
    assert ok_live is False, "En M10, la TX doit être bloquée (hard_disable_send_tx=True)"

    ok_paper = can_send_real_tx(
        cfg,
        ExecutionMode.PAPER_ONCHAIN,
        profile="LIVE_150",
        context="unit_test_paper_mode",
    )

    print(f"can_send_real_tx(PAPER_ONCHAIN, profile=LIVE_150) -> {ok_paper}")
    assert ok_paper is False, "En PAPER_ONCHAIN, aucune TX réelle ne doit jamais partir."

    print("OK: garde-fou TX M10 fonctionne comme attendu (tout est bloqué).")


if __name__ == "__main__":
    main()

