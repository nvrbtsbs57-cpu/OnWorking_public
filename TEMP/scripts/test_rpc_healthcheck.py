from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------
# Bootstrapping du projet (comme pour test_tx_guard_m10)
# ---------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------
# Import RPCClient du projet
# ---------------------------------------------------------------------
try:
    # Version dans bot/core (vue dans le repo TEMP)
    from bot.core.rpc_clients import RPCClient
except ImportError:
    # Fallback au cas où (ancienne version)
    from bot.rpc.client import RPCClient  # type: ignore


CHAINS = [
    # Solana
    {"name": "solana", "env": "RPC_SOLANA_HTTP", "chain_type": "solana"},
    # EVM
    {"name": "base", "env": "RPC_BASE_HTTP", "chain_type": "evm"},
    {"name": "bsc", "env": "RPC_BSC_HTTP", "chain_type": "evm"},
    {"name": "ethereum", "env": "RPC_ETHEREUM_HTTP", "chain_type": "evm"},
    {"name": "arbitrum", "env": "RPC_ARBITRUM_HTTP", "chain_type": "evm"},
]


def main() -> None:
    print("[RPC HEALTHCHECK] QuickNode / RPC HTTP\n")

    for cfg in CHAINS:
        name = cfg["name"]
        env_name = cfg["env"]
        chain_type = cfg["chain_type"]

        url = os.getenv(env_name)
        if not url:
            print(f"[{name}] ❌ {env_name} non défini (pas d’URL)")
            continue

        client = RPCClient(
            name=name,
            rpc_url=url,
            chain_id=None,
            chain_type=chain_type,
        )

        latest = client.get_latest_block()

        if latest is None:
            print(
                f"[{name}] ⚠️ impossible de récupérer un block/slot "
                f"(URL={url})"
            )
        else:
            print(
                f"[{name}] ✅ OK — dernier block/slot = {latest} "
                f"(URL={url})"
            )

    print("\n[RPC HEALTHCHECK] Terminé.")


if __name__ == "__main__":
    main()

