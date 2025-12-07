from pathlib import Path
import json
import sys

# ============================================================================
# Fix PYTHONPATH pour avoir accès au package "bot"
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ============================================================================
# Imports projet
# ============================================================================

# On utilise ta config existante
try:
    from bot.config import load_config  # type: ignore
except ImportError:
    def load_config(path: str):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

# Wallets & Execution
from bot.wallet.manager import WalletManager
from bot.execution.engine import ExecutionEngine, ExecutionRequest, OrderSide, OrderType

# RPC clients (ethereum / arbitrum / base)
try:
    from bot.rpc.client import build_rpc_clients  # type: ignore
except ImportError:
    build_rpc_clients = None


CONFIG_PATH = BASE_DIR / "config.json"


def main():
    print("=== TEST EXECUTION PIPELINE (STUB) ===")
    print("Base dir :", BASE_DIR)
    print("Config   :", CONFIG_PATH)

    # 1) Charger la config (objet/dict via ton loader)
    cfg_obj = load_config(str(CONFIG_PATH))

    # 2) JSON brut pour WalletManager.from_config
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg_raw = json.load(f)

    # 3) RPC clients
    rpc_clients = {}
    if build_rpc_clients is not None:
        rpc_clients = build_rpc_clients(cfg_obj)  # type: ignore
        print("RPC clients init:", list(rpc_clients.keys()))
    else:
        print("ATTENTION: build_rpc_clients introuvable, on utilise un client FAKE pour le test.")

    # 4) WalletManager
    wallet_manager = WalletManager.from_config(cfg_raw)
    wallet_names = wallet_manager.list_wallets()
    print("Wallets dispo:", wallet_names)

    if not wallet_names:
        print("AUCUN wallet dans config['wallets'], test arrêté.")
        return

    # On prend le premier wallet pour le test
    test_wallet = wallet_names[0]
    cfg_w = wallet_manager.get_wallet_config(test_wallet)
    if cfg_w is None:
        print("Config introuvable pour wallet:", test_wallet)
        return

    print(f"Wallet de test: {test_wallet} (chain={cfg_w.chain}, env={cfg_w.private_key_env})")

    # ----------------------------------------------------------------------
    # IMPORTANT : pour le test, on force une FAUSSE clé privée
    # sans toucher aux variables d'environnement ni aux vrais secrets.
    # ----------------------------------------------------------------------
    def fake_get_private_key(name: str) -> str:
        print(f"[FAKE] get_private_key('{name}') appelé -> retourne une clé bidon.")
        return "DUMMY_PRIVATE_KEY_FOR_TEST_ONLY"

    # monkey-patch pour CE SCRIPT SEULEMENT
    wallet_manager.get_private_key = fake_get_private_key  # type: ignore[assignment]

    # ----------------------------------------------------------------------
    # Client RPC FAKE si on n'a pas pu les construire via build_rpc_clients
    # ----------------------------------------------------------------------
    if not rpc_clients:
        rpc_clients = {
            cfg_w.chain: object()  # n'importe quel objet "truthy"
        }
        print(f"[FAKE] RPC client créé pour chain={cfg_w.chain}")

    # 5) ExecutionEngine
    exec_engine = ExecutionEngine(
        rpc_clients=rpc_clients,
        wallet_manager=wallet_manager,
    )

    # 6) Construire une requête d'exécution simple
    req = ExecutionRequest(
        chain=cfg_w.chain,       # chain du wallet (ethereum/base/bsc/solana…)
        symbol_in="USDC",
        symbol_out="WETH",
        amount_in=100.0,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        notional_usd=100.0,
        strategy_tag="manual_test",
        require_tags=[],         # on ne filtre pas par tags pour le test
    )

    print("\nEnvoi de la requête d'exécution STUB...")
    result = exec_engine.execute(req)
    print("\nRésultat ExecutionEngine:")
    print("  success     :", result.success)
    print("  reason      :", result.reason)
    print("  used_wallet :", result.used_wallet)
    print("  tx_hash     :", result.tx_hash)
    print("  extra       :", result.extra)


if __name__ == "__main__":
    main()
