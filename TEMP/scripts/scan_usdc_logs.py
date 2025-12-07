import asyncio
import os
import sys

# === Bootstrapping pour retrouver le package "bot" ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from bot.core.rpc_client import AsyncJsonRpcClient
from bot.indexer.evm_log_decoder import decode_evm_logs

USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # mainnet USDC

# Limites QuickNode Discover : eth_getLogs max range = 5 blocks
MAX_BLOCK_RANGE = 5
TOTAL_BLOCKS_TO_SCAN = 50  # on scanne les 50 derniers blocks


async def main():
    rpc_url = "https://wild-powerful-ensemble.quiknode.pro/fb5ee08a54aab4f75a0b91df37724cab8da6d11b/"
    rpc = AsyncJsonRpcClient(rpc_url)

    try:
        print("🔍 Fetching latest block...")
        latest = await rpc.eth_block_number()
        print(f"Latest block = {latest}")

        start_block = max(latest - TOTAL_BLOCKS_TO_SCAN + 1, 0)
        print(f"🔍 Scanning USDC logs from block {start_block} to {latest} (step={MAX_BLOCK_RANGE})")

        all_decoded = []

        current = start_block
        while current <= latest:
            to_block = min(current + MAX_BLOCK_RANGE - 1, latest)
            print(f"\n➡️  Range {current} - {to_block}")

            logs_raw = await rpc.eth_get_logs(
                from_block=current,
                to_block=to_block,
                addresses=[USDC_ADDRESS],
                topics=None  # tous les events USDC (Transfer, Approval, etc.)
            )

            print(f"   ✔ {len(logs_raw)} raw logs")

            decoded = decode_evm_logs("ethereum", logs_raw)
            print(f"   ✔ {len(decoded)} decoded events")
            all_decoded.extend(decoded)

            # petite pause pour ne pas spammer l'API
            await asyncio.sleep(0.3)

            current = to_block + 1

        print("\n🎯 SUMMARY")
        print(f"Total decoded events = {len(all_decoded)}")

        print("\n📝 SAMPLE (max 10 events):")
        for ev in all_decoded[:10]:
            print("-" * 80)
            print(f"Block: {ev['block_number']}")
            print(f"Tx:    {ev['tx_hash']}")
            print(f"Idx:   {ev['log_index']}")
            print(f"Addr:  {ev['address']}")
            print("Topics:")
            for t in ev['topics']:
                print(f"  {t}")
            print(f"Data:  {ev['data']}")

    finally:
        await rpc.close()
        print("\n✅ RPC session closed.")


if __name__ == "__main__":
    asyncio.run(main())
