import asyncio
import logging
import json
import sys
import os
import time

# Ajuster le path pour que Python trouve le package "bot/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(ROOT)

from bot.agent.engine import AgentEngine, AgentEngineConfig, WhaleAgentConfig
from bot.bot_core.indexer.rpc_client import RPCClient
from bot.bot_core.indexer.token_metadata import TokenMetadataProviderImpl


# =====================================================================================
#     CONFIG LOGGING
# =====================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","name":"%(name)s","msg":"%(message)s"}'
)

logger = logging.getLogger("test_whales_loop")


# =====================================================================================
#     LOAD CONFIG.JSON
# =====================================================================================

def load_config():
    cfg_path = os.path.join(ROOT, "config.json")
    if not os.path.isfile(cfg_path):
        raise FileNotFoundError(f"config.json introuvable : {cfg_path}")

    with open(cfg_path, "r") as f:
        return json.load(f)


# =====================================================================================
#     BUILD RPC CLIENTS + TOKEN META
# =====================================================================================

def build_rpc_clients(config):
    rpc_clients = {}

    indexer_cfg = config.get("indexer", {})
    rpc_urls = indexer_cfg.get("rpc", {})

    for chain, url in rpc_urls.items():
        rpc_clients[chain] = RPCClient(chain=chain, url=url)

    return rpc_clients


def build_token_meta(config):
    return TokenMetadataProviderImpl(
        static_prices=config.get("token_prices", {}),
        static_decimals=config.get("token_decimals", {})
    )


# =====================================================================================
#     LOOP WHALE PIPELINE
# =====================================================================================

async def whale_loop():
    print("\n==============================")
    print("   TEST WHALE PIPELINE LOOP")
    print("==============================\n")

    config = load_config()

    rpc_clients = build_rpc_clients(config)
    token_meta = build_token_meta(config)

    # AgentEngineConfig aligné sur ton config.json optimisé
    chains = config.get("agent", {}).get("chains", list(rpc_clients.keys()) or ["ethereum"])

    whales_cfg_raw = config.get("agent", {}).get("whales", {})
    whales_cfg = WhaleAgentConfig(
        enabled=whales_cfg_raw.get("enabled", True),
        scan_step=whales_cfg_raw.get("scan_step", 5),
        max_blocks_per_loop=whales_cfg_raw.get("max_blocks_per_loop", 50),
        min_block_usd=whales_cfg_raw.get("min_block_usd", 20000),
        high_pressure_threshold=whales_cfg_raw.get("high_pressure_threshold", 75),
        medium_pressure_threshold=whales_cfg_raw.get("medium_pressure_threshold", 40),
        smoothing_alpha=whales_cfg_raw.get("smoothing_alpha", 0.3),
        max_history=whales_cfg_raw.get("max_history", 500),
    )

    agent_cfg = AgentEngineConfig(
        chains=chains,
        whales=whales_cfg,
    )

    agent = AgentEngine(
        config=agent_cfg,
        rpc_clients=rpc_clients,
        token_meta=token_meta,
    )

    print(f"➡️  Chains actives pour les whales : {chains}")
    print("➡️  Loop démarrée. Ctrl+C pour arrêter.\n")

    iteration = 0

    try:
        while True:
            iteration += 1
            decisions = await agent.tick_once()

            if decisions:
                print("\n🟢 NOUVEAUX SIGNAUX WHALES DÉTECTÉS :\n")
                for dec in decisions:
                    d = dec.to_json()
                    print(json.dumps(d, indent=2))
                    print("-" * 60)
            else:
                # Heartbeat léger pour ne pas spam la console
                if iteration % 10 == 0:
                    print(f"[heartbeat] itération={iteration} (aucun whale détecté pour l’instant)")

            # Petit sleep pour ne pas saturer les RPC
            await asyncio.sleep(1.0)

    except KeyboardInterrupt:
        print("\n⛔ Arrêt demandé par l’utilisateur (Ctrl+C).")
        print("✔ Loop whales stoppée proprement.\n")


def main():
    # Wrapper sync pour lancer l’async proprement
    asyncio.run(whale_loop())


if __name__ == "__main__":
    main()
