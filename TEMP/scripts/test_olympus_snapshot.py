from __future__ import annotations

import os
import sys
import json
from pathlib import Path
from pprint import pprint

ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from bot.wallets.factory import build_wallet_engine_from_config
from bot.olympus.service import build_finance_snapshot_from_wallet_engine


def main() -> None:
    cfg_path = Path(ROOT_DIR) / "config.json"
    with cfg_path.open("r", encoding="utf-8") as f:
        raw_cfg = json.load(f)

    engine = build_wallet_engine_from_config(raw_cfg, logger=None)

    snapshot = build_finance_snapshot_from_wallet_engine(engine)

    # Pydantic v2 → utiliser model_dump()
    pprint(snapshot.model_dump())


if __name__ == "__main__":
    main()
