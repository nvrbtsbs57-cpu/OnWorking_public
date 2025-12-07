from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

# ----------------------------------------------------------------------
# Bootstrap du projet (comme les autres scripts)
# ----------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parents[1]  # .../BOT_GODMODE
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from bot.finance.runtime_manager import RuntimeWalletManager  # type: ignore


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )
    logger = logging.getLogger("finance_debug_wallets")

    cfg_path = ROOT_DIR / "config.json"
    logger.info("Base dir  : %s", ROOT_DIR)
    logger.info("Config    : %s", cfg_path)

    with cfg_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    # Instancie le moteur finance + RuntimeWalletManager
    wm = RuntimeWalletManager.from_config(raw_cfg, logger=logger.getChild("RuntimeWalletManager"))

    snap = wm.debug_snapshot()
    total_equity = wm.get_total_equity_usd()

    logger.info("Wallets   : %d", len(snap))
    logger.info("Equity USD: %s", total_equity)

    # Chemin attendu du snapshot
    runtime_path = ROOT_DIR / "data" / "godmode" / "wallets_runtime.json"
    logger.info("Snapshot écrit dans : %s", runtime_path)


if __name__ == "__main__":
    main()
