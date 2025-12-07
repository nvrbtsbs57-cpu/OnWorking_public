class BotError(Exception):
    """Base class for all bot errors."""
    code = "BOT_ERROR"


class ConfigError(BotError):
    code = "CONFIG_ERROR"


class ChainError(BotError):
    code = "CHAIN_ERROR"


class IndexerError(BotError):
    code = "INDEXER_ERROR"


class NormalizerError(BotError):
    code = "NORMALIZER_ERROR"


class AgentError(BotError):
    code = "AGENT_ERROR"


class ApiError(BotError):
    code = "API_ERROR"
