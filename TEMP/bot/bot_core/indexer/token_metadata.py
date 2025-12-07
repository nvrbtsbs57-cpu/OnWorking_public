from __future__ import annotations

from typing import Optional


class TokenMetadataProvider:
    """
    Interface minimale utilisée par le WhaleScanner :
      - get_token_decimals(chain, token_address)
      - get_token_price_usd(chain, token_address)

    Le bot définit une implémentation simple basée sur des tables locales
    (config.json) ou on pourra étendre plus tard avec Coingecko / API on-chain.
    """

    def get_token_decimals(self, chain: str, token: str) -> int:
        raise NotImplementedError

    def get_token_price_usd(self, chain: str, token: str) -> float:
        raise NotImplementedError


class TokenMetadataProviderImpl(TokenMetadataProvider):
    """
    Implémentation basique utilisée par le bot pour le WhaleScanner.
    """

    def __init__(self, static_prices: dict, static_decimals: dict):
        # Exemple :
        # static_decimals = {"ethereum": {"0xa0b86991...": 6}}
        # static_prices = {"ethereum": {"0xa0b86991...": 1.00}}
        self.static_prices = static_prices or {}
        self.static_decimals = static_decimals or {}

    def get_token_decimals(self, chain: str, token: str) -> int:
        return (
            self.static_decimals
            .get(chain, {})
            .get(token.lower(), 18)
        )

    def get_token_price_usd(self, chain: str, token: str) -> float:
        return (
            self.static_prices
            .get(chain, {})
            .get(token.lower(), 0.0)
        )
