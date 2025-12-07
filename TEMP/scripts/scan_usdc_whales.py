import asyncio
import os
import sys

# === Bootstrapping pour retrouver le package "bot" ===
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from bot.core.rpc_client import AsyncJsonRpcClient
from bot.indexer.evm_log_decoder import (
    decode_evm_logs,
    EvmLogEvent,
    extract_erc20_transfer,
)

USDC_ADDRESS = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"  # mainnet USDC (lowercase)
MAX_BLOCK_RANGE = 5          # limite QuickNode Discover
TOTAL_BLOCKS_TO_SCAN = 50    # on scanne les 50 derniers blocks
USDC_DECIMALS = 6
WHALE_THRESHOLD = 50_000.0   # seuil "whale" en USDC
TOP_TRANSFERS_TO_SHOW = 20   # on affiche les 20 plus gros transferts


def evm_log_dict_to_dataclass(ev_dict) -> EvmLogEvent:
    """
    Convertit un dict produit par decode_evm_logs en EvmLogEvent.
    """
    return EvmLogEvent(
        chain=ev_dict["chain"],
        block_number=ev_dict["block_number"],
        tx_hash=ev_dict["tx_hash"],
        log_index=ev_dict["log_index"],
        address=ev_dict["address"],
        topics=ev_dict["topics"],
        data=ev_dict["data"],
        metadata=ev_dict.get("metadata", {}),
    )


async def main():
    rpc_url = "https://wild-powerful-ensemble.quiknode.pro/fb5ee08a54aab4f75a0b91df37724cab8da6d11b/"
    rpc = AsyncJsonRpcClient(rpc_url)

    transfers = []

    try:
        print("🔍 Fetching latest block...")
        latest = await rpc.eth_block_number()
        print(f"Latest block = {latest}")

        start_block = max(latest - TOTAL_BLOCKS_TO_SCAN + 1, 0)
        print(f"🔍 Scanning USDC whales from block {start_block} to {latest} (step={MAX_BLOCK_RANGE})")

        current = start_block
        while current <= latest:
            to_block = min(current + MAX_BLOCK_RANGE - 1, latest)
            print(f"\➡️  Range {current} - {to_block}")

            logs_raw = await rpc.eth_get_logs(
                from_block=current,
                to_block=to_block,
                addresses=[USDC_ADDRESS],
                topics=None,
            )

            print(f"   ✔ {len(logs_raw)} raw logs")

            decoded_dicts = decode_evm_logs("ethereum", logs_raw)
            print(f"   ✔ {len(decoded_dicts)} decoded logs")

            # Conversion en dataclasses pour utiliser extract_erc20_transfer
            for d in decoded_dicts:
                ev = evm_log_dict_to_dataclass(d)
                transfer = extract_erc20_transfer(ev, decimals=USDC_DECIMALS)
                if not transfer:
                    continue

                transfers.append(transfer)

            await asyncio.sleep(0.3)
            current = to_block + 1

    finally:
        await rpc.close()
        print("\n✅ RPC session closed.")

    if not transfers:
        print("\n🐋 USDC WHALES DETECTED")
        print("Aucun transfert USDC détecté dans cette fenêtre de blocks.")
        return

    # Tri par montant décroissant
    transfers_sorted = sorted(transfers, key=lambda x: x["value"], reverse=True)

    print("\n🐋 TOP USDC TRANSFERS")
    for t in transfers_sorted[:TOP_TRANSFERS_TO_SHOW]:
        is_whale = t["value"] >= WHALE_THRESHOLD
        tag = "🐋 WHALE" if is_whale else "•"
        print("-" * 80)
        print(f"{tag}  Block:   {t['block_number']}")
        print(f"    Tx:      {t['tx_hash']}")
        print(f"    From:    {t['from_address']}")
        print(f"    To:      {t['to_address']}")
        print(f"    Amount:  {t['value']:,.2f} USDC")

    # Résumé des whales si il y en a
    whales = [t for t in transfers_sorted if t["value"] >= WHALE_THRESHOLD]

    print("\n📊 SUMMARY")
    print(f"Total transfers: {len(transfers)}")
    print(f"Whales (≥ {WHALE_THRESHOLD:,.0f} USDC): {len(whales)}")
    if whales:
        biggest = whales[0]
        print(f"Biggest whale: {biggest['value']:,.2f} USDC (tx {biggest['tx_hash']})")


if __name__ == "__main__":
    asyncio.run(main())
