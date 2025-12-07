#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_finance_jobs.py

Job Finance autonome :
- Charge la config globale (config.json)
- Construit FinancePipeline (AutoFees + Sweep + Compounding)
- Charge des snapshots de wallets depuis data/finance/wallet_snapshots.json
- Génère des plans et les log + enregistre dans data/finance/plans_*.jsonl

AUCUNE exécution on-chain ici, juste du planning.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any

# ======================================================================
# PYTHONPATH
# ======================================================================

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ======================================================================
# Imports projet
# ======================================================================

from bot.finance.pipeline import (
    FinanceConfig,
    FinancePipeline,
    WalletSnapshot,
    TransferPlan,
)

# ======================================================================
# Logging
# ======================================================================

logger = logging.getLogger("run_finance_jobs")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# ======================================================================
# Helpers
# ======================================================================

def load_global_config() -> Dict[str, Any]:
    cfg_path = BASE_DIR / "config.json"
    if not cfg_path.exists():
        raise SystemExit(f"[FATAL] config.json introuvable: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_wallet_snapshots(path: Path) -> Dict[str, WalletSnapshot]:
    """
    Charge un fichier JSON de snapshots, format attendu :

    {
      "sniper_sol": {
        "chain": "solana",
        "role": "SCALPING",
        "balance_native": "0.12",
        "balance_usd": "80.5",
        "realized_profit_usd": "15.3",
        "tags": ["sniper","sol"]
      },
      "fees": {
        "chain": "ethereum",
        "role": "AUTO_FEES",
        "balance_native": "0.4",
        "balance_usd": "200",
        "realized_profit_usd": "0",
        "tags": ["fees","gas"]
      }
    }
    """
    if not path.exists():
        logger.warning("Fichier snapshots absent: %s", path)
        return {}

    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    snapshots: Dict[str, WalletSnapshot] = {}
    for name, data in raw.items():
        try:
            chain = str(data.get("chain", "")).lower()
            role = data.get("role")
            balance_native = Decimal(str(data.get("balance_native", "0")))
            balance_usd = Decimal(str(data.get("balance_usd", "0")))
            realized_profit_usd = Decimal(str(data.get("realized_profit_usd", "0")))
            tags = data.get("tags") or []
            if not isinstance(tags, list):
                tags = []

            snapshots[name] = WalletSnapshot(
                name=name,
                chain=chain,
                role=role,
                balance_native=balance_native,
                balance_usd=balance_usd,
                realized_profit_usd=realized_profit_usd,
                tags=tags,
            )
        except Exception as exc:
            logger.exception("Erreur lors du parsing du snapshot '%s': %s", name, exc)

    return snapshots


def save_plans_to_jsonl(plans: list[TransferPlan], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"plans_{ts}.jsonl"

    with out_path.open("w", encoding="utf-8") as f:
        for p in plans:
            line = {
                "type": p.type,
                "from_wallet": p.from_wallet,
                "to_wallet": p.to_wallet,
                "chain": p.chain,
                "amount_native": str(p.amount_native),
                "amount_usd": str(p.amount_usd),
                "reason": p.reason,
                "metadata": p.metadata,
            }
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    return out_path


def log_plans(plans: list[TransferPlan]) -> None:
    if not plans:
        logger.info("Aucun plan Finance à exécuter pour ce run.")
        return

    logger.info("Plans Finance générés (%d):", len(plans))
    for p in plans:
        logger.info(
            "[%s] %s -> %s | chain=%s | native=%s | usd=%s | reason=%s",
            p.type,
            p.from_wallet,
            p.to_wallet,
            p.chain,
            p.amount_native,
            p.amount_usd,
            p.reason,
        )


# ======================================================================
# main()
# ======================================================================

def main() -> None:
    setup_logging()
    logger.info("=== Finance Jobs (AutoFees / Sweep / Compounding) ===")

    cfg = load_global_config()

    wallet_roles = cfg.get("wallet_roles", {})
    wallets_cfg = cfg.get("wallets", [])

    finance_cfg = FinanceConfig.from_global_config(cfg)
    pipeline = FinancePipeline(
        config=finance_cfg,
        wallet_roles=wallet_roles,
        wallets_cfg=wallets_cfg,
    )

    snapshots_path = BASE_DIR / "data" / "finance" / "wallet_snapshots.json"
    snapshots = load_wallet_snapshots(snapshots_path)

    if not snapshots:
        logger.warning(
            "Aucun snapshot de wallet chargé (path=%s). "
            "Aucun plan ne sera généré.",
            snapshots_path,
        )
        return

    plans = pipeline.plan_all(snapshots)
    log_plans(plans)

    out_dir = BASE_DIR / "data" / "finance" / "plans"
    out_path = save_plans_to_jsonl(plans, out_dir)
    logger.info("Plans Finance enregistrés dans %s", out_path)


if __name__ == "__main__":
    main()
