# bot/indexer/evm_log_decoder.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Sequence

from bot.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# Constantes de topics ERC-20
# ============================================================================

# keccak256("Transfer(address,address,uint256)")
ERC20_TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# keccak256("Approval(address,address,uint256)")
ERC20_APPROVAL_TOPIC = (
    "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
)


@dataclass
class EvmLogEvent:
    """
    Représentation normalisée d'un log EVM brut.

    On garde quelque chose de très générique, suffisant pour :
      - savoir sur quel chain / block / tx on est
      - filtrer par address / topics
      - décoder ensuite (ERC20, Uniswap, etc.)
    """

    chain: str
    block_number: int
    tx_hash: str
    log_index: int

    address: str
    topics: List[str]
    data: str

    # champ libre pour ajouter des infos plus tard (decoded_event, label, etc.)
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, str) and value.startswith("0x"):
            return int(value, 16)
        return int(value)
    except Exception:
        return default


def _extract_tx_hash(raw: Dict[str, Any]) -> str:
    # eth_getLogs retourne "transactionHash"
    h = raw.get("transactionHash")
    return h if isinstance(h, str) else ""


def _extract_log_index(raw: Dict[str, Any]) -> int:
    idx = raw.get("logIndex")
    return _safe_int(idx, 0)


def decode_single_log(chain_name: str, raw_log: Dict[str, Any]) -> EvmLogEvent:
    """
    Transforme un log EVM brut en EvmLogEvent.

    `raw_log` compatible avec ce que renvoie `eth_getLogs` :
    - address
    - topics[]
    - data
    - blockNumber
    - transactionHash
    - logIndex
    etc.
    """
    block_number = _safe_int(raw_log.get("blockNumber"), 0)
    tx_hash = _extract_tx_hash(raw_log)
    log_index = _extract_log_index(raw_log)

    address = raw_log.get("address") or ""
    if not isinstance(address, str):
        address = str(address)

    topics_raw = raw_log.get("topics") or []
    topics: List[str] = []
    if isinstance(topics_raw, (list, tuple)):
        for t in topics_raw:
            if isinstance(t, str):
                topics.append(t.lower())
            else:
                topics.append(str(t).lower())

    data = raw_log.get("data") or "0x"
    if not isinstance(data, str):
        data = str(data)
    data = data.lower()

    ev = EvmLogEvent(
        chain=chain_name,
        block_number=block_number,
        tx_hash=tx_hash,
        log_index=log_index,
        address=address.lower(),
        topics=topics,
        data=data,
        metadata={},
    )

    logger.debug(
        f"Decoded EVM log chain={chain_name} block={block_number} "
        f"tx={tx_hash} idx={log_index} addr={address}"
    )

    return ev


def decode_evm_logs(chain_name: str, logs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Décode une liste de logs bruts en liste de dicts prêts à être stockés
    par l'indexer (FileStorage).

    Retourne une liste de `dict` (pas les dataclasses),
    pour être directement sérialisable en JSON.
    """
    events: List[Dict[str, Any]] = []

    for raw in logs:
        try:
            ev = decode_single_log(chain_name, raw)
            events.append(ev.to_dict())
        except Exception as e:
            logger.error(f"Failed to decode EVM log on chain={chain_name}: {e}")

    logger.debug(f"Decoded {len(events)} EVM logs for chain={chain_name}")
    return events


# ============================================================================
# Helpers ERC-20
# ============================================================================

def _topic_to_address(topic: str) -> str:
    """
    Convertit un topic de type "0x0000...<40 hex>" en address EVM
    ex: 0x000000000000000000000000a0b8... => 0xa0b8...
    """
    if not isinstance(topic, str) or not topic.startswith("0x") or len(topic) < 2 + 40:
        return ""
    # on prend les 40 derniers caractères hex
    return "0x" + topic[-40:]


def _data_to_int(data: str) -> int:
    if isinstance(data, str) and data.startswith("0x"):
        try:
            return int(data, 16)
        except Exception:
            return 0
    try:
        return int(data)
    except Exception:
        return 0


def extract_erc20_transfer(
    ev: EvmLogEvent,
    decimals: int = 18,
) -> Optional[Dict[str, Any]]:
    """
    Si le log est un event ERC-20 Transfer(address,address,uint256),
    retourne un dict normalisé contenant :
      - chain
      - token_address
      - block_number
      - tx_hash
      - log_index
      - from_address
      - to_address
      - value_raw (int)
      - value (float) : value_raw / 10**decimals
    Sinon, retourne None.
    """
    if not ev.topics:
        return None

    if ev.topics[0] != ERC20_TRANSFER_TOPIC:
        return None

    from_addr = _topic_to_address(ev.topics[1]) if len(ev.topics) > 1 else ""
    to_addr = _topic_to_address(ev.topics[2]) if len(ev.topics) > 2 else ""
    value_raw = _data_to_int(ev.data)

    value = float(value_raw) / (10 ** decimals) if decimals > 0 else float(value_raw)

    return {
        "chain": ev.chain,
        "token_address": ev.address,
        "block_number": ev.block_number,
        "tx_hash": ev.tx_hash,
        "log_index": ev.log_index,
        "from_address": from_addr,
        "to_address": to_addr,
        "value_raw": value_raw,
        "value": value,
    }
