import asyncio

from bot.core.logging import get_logger
from bot.normalizer.normalizer_engine import NormalizerEngine
from .state import AgentState
from .strategy_engine import StrategyEngine
from .risk_engine import RiskEngine
from .alerts_engine import AlertsEngine

logger = get_logger(__name__)


class AgentRouter:
    def __init__(self, normalizer: NormalizerEngine) -> None:
        self.normalizer = normalizer
        self.state = AgentState()
        self.strategy = StrategyEngine()
        self.risk = RiskEngine()
        self.alerts = AlertsEngine()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        logger.info("AgentRouter started")
        await asyncio.gather(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        logger.info("AgentRouter stop requested")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                events = self.normalizer.get_recent_events()
                self.state.update(events)
                raw_alerts = self.strategy.evaluate(self.state.events)
                filtered = self.risk.filter_alerts(raw_alerts)
                self.alerts.dispatch(filtered)
                await asyncio.sleep(2.0)
            except Exception as e:
                logger.error(f"Error in AgentRouter: {e}")
                await asyncio.sleep(2.0)
