from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Deque
from collections import deque

from bot.normalizer.models import NormalizedTick
from .events import ActivitySpikeEvent, VolumeSpikeEvent, ImpactSpikeEvent, BaseEvent

logger = logging.getLogger(__name__)


@dataclass
class MapperState:
    events: Deque[BaseEvent] = field(default_factory=lambda: deque(maxlen=5000))
    last_activity: float = 0.0
    last_volume: float = 0.0
    last_impact: float = 0.0


class EventMapper:
    """
    MAPPER PRO — GODMODE
    Transforme les ticks normalisés en événements intelligents :

    - Activity spikes
    - Volume spikes
    - Impact spikes
    """

    def __init__(self) -> None:
        self.state = MapperState()
        self._stop_event = threading.Event()
        self._tick_buffer: Deque[NormalizedTick] = deque(maxlen=5000)

    # -----------------------------------------------------------
    # Public API
    # -----------------------------------------------------------

    def push_tick(self, tick: NormalizedTick) -> None:
        self._tick_buffer.append(tick)

    def get_events(self) -> List[BaseEvent]:
        return list(self.state.events)

    # -----------------------------------------------------------
    # Thread runner
    # -----------------------------------------------------------

    def start_in_thread(self, name: str = "mapper") -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name=name, daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self) -> None:
        logger.info("MapperEngine PRO started")
        while not self._stop_event.is_set():
            try:
                self._process_ticks()
            except Exception:
                logger.exception("Error in mapper loop")

            time.sleep(0.5)

    # -----------------------------------------------------------
    # Core mapping logic
    # -----------------------------------------------------------

    def _process_ticks(self) -> None:
        if not self._tick_buffer:
            return

        while self._tick_buffer:
            tick = self._tick_buffer.popleft()
            self._map_tick(tick)

    def _map_tick(self, tick: NormalizedTick) -> None:
        # Activity spike detection
        if tick.activity_level > self.state.last_activity * 1.8 and tick.activity_level > 0.1:
            severity = self._severity(tick.activity_level)
            event = ActivitySpikeEvent(
                chain=tick.chain,
                block_number=tick.block_number,
                timestamp=tick.timestamp,
                type="activity_spike",
                tx_count=tick.tx_count,
                activity_level=tick.activity_level,
                severity=severity
            )
            self.state.events.append(event)
            logger.info("Mapped ActivitySpikeEvent — severity=%s", severity)

        # Volume spike detection
        if tick.volume_estimate > self.state.last_volume * 2.0 and tick.volume_estimate > 0.05:
            severity = self._severity(tick.volume_estimate)
            event = VolumeSpikeEvent(
                chain=tick.chain,
                block_number=tick.block_number,
                timestamp=tick.timestamp,
                type="volume_spike",
                volume_estimate=tick.volume_estimate,
                severity=severity
            )
            self.state.events.append(event)
            logger.info("Mapped VolumeSpikeEvent — severity=%s", severity)

        # Impact spike detection
        if tick.price_impact_estimate > self.state.last_impact * 2.0 and tick.price_impact_estimate > 0.05:
            severity = self._severity(tick.price_impact_estimate)
            event = ImpactSpikeEvent(
                chain=tick.chain,
                block_number=tick.block_number,
                timestamp=tick.timestamp,
                type="impact_spike",
                impact_value=tick.price_impact_estimate,
                severity=severity
            )
            self.state.events.append(event)
            logger.info("Mapped ImpactSpikeEvent — severity=%s", severity)

        # Update last values
        self.state.last_activity = max(self.state.last_activity, tick.activity_level)
        self.state.last_volume = max(self.state.last_volume, tick.volume_estimate)
        self.state.last_impact = max(self.state.last_impact, tick.price_impact_estimate)

    # -----------------------------------------------------------
    # Utility
    # -----------------------------------------------------------

    @staticmethod
    def _severity(value: float) -> str:
        if value > 5.0:
            return "CRITICAL"
        if value > 2.0:
            return "HIGH"
        if value > 1.0:
            return "MEDIUM"
        return "LOW"
