from typing import Dict, List

from .base_chain import ChainConfig, BaseChain
from .evm_chain import EvmChain


class ChainRegistry:
    def __init__(self) -> None:
        self._chains: Dict[str, BaseChain] = {}

    def register_from_config(self, chains_config: List[dict]) -> None:
        for cfg in chains_config:
            log_addresses = cfg.get("log_addresses", [])
            # On normalise en lowercase
            if isinstance(log_addresses, list):
                log_addresses = [str(a).lower() for a in log_addresses]
            else:
                log_addresses = []

            chain_cfg = ChainConfig(
                name=cfg["name"],
                type=cfg["type"],
                rpc_url=cfg["rpc_url"],
                chain_id=cfg["chain_id"],
                enabled=cfg.get("enabled", True),
                log_addresses=log_addresses,
            )
            if not chain_cfg.enabled:
                continue

            if chain_cfg.type == "evm":
                chain = EvmChain(chain_cfg)
            else:
                # Future: solana_chain, ton_chain, ...
                continue

            self._chains[chain_cfg.name] = chain

    def get_all(self) -> Dict[str, BaseChain]:
        return self._chains

    def get(self, name: str) -> BaseChain:
        return self._chains[name]
