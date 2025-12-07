from __future__ import annotations

import asyncio
from typing import Dict, Optional

from bot.core.logging import get_logger
from bot.chains.base_chain import BaseChain
from .storage import IndexerStorage

AbstractStorage = IndexerStorage

from .clients.evm_client import EvmIndexerClient

logger = get_logger(__name__)


class IndexerEngine:
    """
    Indexer multi-chain "FULL PRO" :

    - boucle asynchrone par chain (Ethereum, Arbitrum, Base, …)
    - limite le nombre de blocs indexés par tick (max_blocks_per_poll)
    - backoff exponentiel en cas d'erreur, avec escalade WARNING/ERROR
    - logs structurés pour AlertEngine (event=indexer_*)
    """

    def __init__(
        self,
        chains: Dict[str, BaseChain],
        storage: AbstractStorage,
        poll_interval: float = 2.0,
        max_blocks_per_poll: int = 25,
        *,
        # nb d'erreurs consécutives avant de passer en ERROR
        failure_error_threshold: int = 3,
        # lag (en blocs) au-delà duquel on log un warning "indexer_lag"
        lag_warning_threshold: int = 100,
    ) -> None:
        self.chains = chains
        self.storage = storage
        self.poll_interval = poll_interval
        self.max_blocks_per_poll = max_blocks_per_poll
        self.failure_error_threshold = failure_error_threshold
        self.lag_warning_threshold = lag_warning_threshold

        self._stop = asyncio.Event()
        self._tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Démarre une task par chain et attend qu'elles se terminent.
        """
        if not self.chains:
            logger.warning(
                "IndexerEngine démarré sans chaines configurées.",
                extra={"event": "indexer_no_chains"},
            )
            return

        logger.info(
            "IndexerEngine started (chains=%s, poll_interval=%.2fs, max_blocks_per_poll=%d)",
            ",".join(self.chains.keys()),
            self.poll_interval,
            self.max_blocks_per_poll,
        )

        loop = asyncio.get_running_loop()
        self._tasks = {
            name: loop.create_task(self._run_chain(name, chain), name=f"indexer:{name}")
            for name, chain in self.chains.items()
        }

        try:
            await asyncio.gather(*self._tasks.values())
        finally:
            logger.info("IndexerEngine stopped")

    async def stop(self) -> None:
        """
        Demande l'arrêt propre de toutes les boucles.
        """
        if self._stop.is_set():
            return

        logger.info("IndexerEngine stop requested")
        self._stop.set()

        # On laisse un peu de temps pour que les tasks se terminent proprement.
        await asyncio.sleep(0.1)

        for name, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                logger.info("Task indexer pour chain=%s annulée", name)

    # ------------------------------------------------------------------    # Loop par chain
    # ------------------------------------------------------------------

    async def _run_chain(self, name: str, chain: BaseChain) -> None:
        client = EvmIndexerClient(chain)
        logger.info(
            "Indexer loop started for chain=%s",
            name,
            extra={"event": "indexer_start", "chain": name},
        )

        failure_streak = 0

        while not self._stop.is_set():
            try:
                last_block = self.storage.get_last_block(name)
                latest_block = await client.fetch_latest_block()

                # Rien de nouveau -> idle
                if latest_block <= last_block:
                    logger.debug(
                        "Indexer idle for chain=%s (last=%d, latest=%d)",
                        name,
                        last_block,
                        latest_block,
                    )
                    await asyncio.sleep(self.poll_interval)
                    continue

                # Limite du nombre de blocs à indexer par tick
                to_block = min(latest_block, last_block + self.max_blocks_per_poll)

                events = await client.fetch_events_range(last_block + 1, to_block)
                self.storage.save_events(name, events)
                self.storage.set_last_block(name, to_block)

                indexed_blocks = to_block - last_block
                lag_after = latest_block - to_block

                logger.info(
                    "Indexed chain=%s blocks %d-%d events=%d latest_on_chain=%d lag_after=%d",
                    name,
                    last_block + 1,
                    to_block,
                    len(events),
                    latest_block,
                    lag_after,
                    extra={
                        "event": "indexer_tick",
                        "chain": name,
                        "from_block": last_block + 1,
                        "to_block": to_block,
                        "indexed_blocks": indexed_blocks,
                        "events": len(events),
                        "latest_block": latest_block,
                        "lag_after": lag_after,
                    },
                )

                # Lag important -> warning structuré (capté par AlertEngine)
                if lag_after >= self.lag_warning_threshold:
                    logger.warning(
                        "Indexer lag élevé pour chain=%s (lag_after=%d blocs)",
                        name,
                        lag_after,
                        extra={
                            "event": "indexer_lag",
                            "chain": name,
                            "lag_after": lag_after,
                            "latest_block": latest_block,
                            "last_indexed_block": to_block,
                        },
                    )

                # reset backoff si tout va bien
                failure_streak = 0

                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                # Arrêt propre demandé
                logger.info(
                    "Indexer loop cancelled for chain=%s",
                    name,
                    extra={"event": "indexer_cancelled", "chain": name},
                )
                break

            except Exception as e:
                failure_streak += 1
                backoff = min(self.poll_interval * (2 ** failure_streak), 60.0)

                log_func = logger.warning
                level_str = "WARNING"
                if failure_streak >= self.failure_error_threshold:
                    log_func = logger.error
                    level_str = "ERROR"

                log_func(
                    "Error in indexer loop for chain=%s "
                    "(failure_streak=%d, backoff=%.1fs, level=%s): %s",
                    name,
                    failure_streak,
                    backoff,
                    level_str,
                    e,
                    extra={
                        "event": "indexer_error",
                        "chain": name,
                        "failure_streak": failure_streak,
                        "backoff_seconds": backoff,
                        "level": level_str,
                    },
                )

                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    logger.info(
                        "Indexer loop cancelled during backoff for chain=%s",
                        name,
                        extra={"event": "indexer_cancelled", "chain": name},
                    )
                    break
