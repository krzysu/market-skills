"""Retry helper for L0 provider fetches.

Single source of truth for transient-failure resilience. Used by every
``fetch()`` / ``fetch_spot_price()`` in the providers package.

Why here, not in the callers:
  - All callers benefit without duplicating retry logic.
  - The provider knows which exceptions are transient vs structural
    for its protocol (timeout vs JSON parse, connection refused vs 4xx).
  - Centralised knobs (attempts, base_delay, jitter) live in one place.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Default knobs. Override per call when the caller knows better.
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY_S = 1.0
DEFAULT_JITTER_S = 0.3

# Transient exceptions for HTTP / network-based providers (yfinance,
# ccxt‑via‑requests, etc.).  Excludes OSError because FileNotFoundError
# (a subclass) is a structural error (missing binary), not a transient
# network glitch.  Subprocess‑based providers (kraken CLI) should pass
# their own narrow tuple like (subprocess.TimeoutExpired,).
TRANSIENT_NETWORK: tuple[type[BaseException], ...] = (
    ConnectionError,  # requests / httpx / ccxt network layer
    TimeoutError,  # built-in timeout
)


def _sleep(i: int, base_delay: float, jitter: float) -> float:
    """Compute exponential backoff with jitter. Returns the seconds slept."""
    sleep_s = base_delay * (2**i) + random.uniform(0, jitter)
    time.sleep(sleep_s)
    return sleep_s


def with_retry[T](
    fn: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_S,
    jitter: float = DEFAULT_JITTER_S,
    transient: tuple[type[BaseException], ...] = TRANSIENT_NETWORK,
    transient_result: Callable[[T], bool] | None = None,
    logger: logging.Logger | None = None,
    label: str = "fetch",
) -> T:
    """Run ``fn()`` up to ``attempts`` times.

    Returns the first successful value. Raises the last exception
    after ``attempts`` failures. Non-transient exceptions propagate
    immediately (no retry on schema / JSON / HTTP 4xx errors).

    If ``transient_result`` is provided, it is called on each non-raising
    result. A ``True`` return marks the result as transiently bad (e.g.
    API-error body with rc=0) and triggers the same backoff-and-retry
    flow as a caught exception. On exhaustion the bad result is
    returned (preserves the consumer contract of ``None``/``[]``/``False``
    on hard failure rather than raising).
    """
    last_exc: BaseException | None = None
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    for i in range(attempts):
        try:
            result = fn()
        except transient as e:
            last_exc = e
            if i == attempts - 1:
                break
            slept = _sleep(i, base_delay, jitter)
            if logger:
                logger.warning(
                    "%s transient failure on attempt %d/%d (%s: %s); retrying after %.2fs",
                    label,
                    i + 1,
                    attempts,
                    type(e).__name__,
                    e,
                    slept,
                )
            continue
        if transient_result is not None and transient_result(result):
            if i == attempts - 1:
                if logger:
                    logger.warning(
                        "%s transient result on attempt %d/%d; out of attempts, returning bad result",
                        label,
                        i + 1,
                        attempts,
                    )
                return result
            slept = _sleep(i, base_delay, jitter)
            if logger:
                logger.warning(
                    "%s transient result on attempt %d/%d; retrying after %.2fs",
                    label,
                    i + 1,
                    attempts,
                    slept,
                )
            continue
        return result
    assert last_exc is not None  # only reachable when attempts >= 1 and every attempt raised
    raise last_exc
