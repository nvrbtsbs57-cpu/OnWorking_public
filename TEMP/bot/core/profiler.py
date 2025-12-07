import time
from contextlib import contextmanager
from typing import Iterator

from .logging import get_logger

logger = get_logger(__name__)


@contextmanager
def profile_block(name: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        logger.debug(f"[PROFILE] {name} took {duration:.6f}s")
