#!/usr/bin/env python
# scripts/test_execution_risk_live150.py

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------
# Forcer le répertoire racine du projet dans sys.path
# ---------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Maintenant on peut importer le package "bot"
from bot.trading.execution_with_risk import (  # type: ignore
    build_execution_with_risk_from_config,
    get_execution_status_snapshot,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_execution_risk_live150")


def main() -> None:
    config_path = BASE_DIR / "config.json"

    logger.info("Chargement config depuis %s", config_path)
    with config_path.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    run_mode = cfg.get("RUN_MODE", "paper")
    logger.info("RUN_MODE = %s", run_mode)

    logger.info("Construction de ExecutionWithRisk depuis la config...")
    execution = build_execution_with_risk_from_config(
        raw_cfg=cfg,
        wallet_manager=None,   # en test : pas de RuntimeWalletManager
        base_dir=BASE_DIR,     # accepté par le builder (compat)
        run_mode=run_mode,     # idem
    )
    logger.info("ExecutionWithRisk construit: %s", type(execution).__name__)

    logger.info(
        "Attente 2s pour laisser le snapshot writer créer/mettre à jour "
        "execution_runtime.json..."
    )
    time.sleep(2.0)

    # On lit le snapshot directement depuis l'instance (pas besoin de relire le fichier)
    snapshot = get_execution_status_snapshot(execution)
    logger.info("Snapshot d'exécution courant:")
    print(json.dumps(snapshot, indent=2, default=str))


if __name__ == "__main__":
    main()

