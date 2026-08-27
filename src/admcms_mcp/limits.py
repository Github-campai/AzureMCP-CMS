"""Per-user concurrency limit on tool calls.

`query_database` is free-form SQL with a 180-second timeout, running against a
pool of eight connections on half a vCPU. One person asking three heavy
questions at once can hold every connection and make the server useless for
everyone else — and there is nothing in front of it that says no.

A concurrency limit fits the problem better than a calls-per-minute counter:
what hurts here is how long a query runs, not how often one is asked. Someone
firing twenty cheap lookups a minute costs nothing; someone holding four
twelve-month aggregations costs everything.

Waiting is the default answer, not refusing. A dashboard opens several tiles at
once and those are legitimate; they queue and arrive a moment later. Only when
the wait itself runs long does the caller get a refusal, and then it says so in
words a person can act on.

State is per replica and in memory. With `maxReplicas: 3` the effective ceiling
is therefore up to three times the configured limit if a user's requests land on
different replicas. That is accepted: this is a brake on one person saturating
one server, not a quota.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 3
DEFAULT_QUEUE_TIMEOUT_SECONDS = 60.0


class ConcurrencyLimitReached(Exception):  # noqa: N818 — reads as the condition, not an error
    """Raised when a caller's slot does not free up in time."""


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %s", name, raw, default)
        return default


def max_concurrent() -> int:
    """Concurrent tool calls allowed per caller. 0 or less disables the limit."""
    return _int_env("MAX_CONCURRENT_QUERIES_PER_USER", DEFAULT_MAX_CONCURRENT)


def queue_timeout_seconds() -> float:
    """How long a call waits for a slot before it is refused."""
    return _float_env("QUERY_QUEUE_TIMEOUT_SECONDS", DEFAULT_QUEUE_TIMEOUT_SECONDS)


class UserConcurrencyLimiter:
    """One semaphore per caller, created on demand and dropped when idle."""

    def __init__(self) -> None:
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._holders: dict[str, int] = {}

    def _acquire_semaphore(self, subject: str, limit: int) -> asyncio.Semaphore:
        semaphore = self._semaphores.get(subject)
        if semaphore is None:
            semaphore = asyncio.Semaphore(limit)
            self._semaphores[subject] = semaphore
            self._holders[subject] = 0
        self._holders[subject] += 1
        return semaphore

    def _release_semaphore(self, subject: str) -> None:
        self._holders[subject] = self._holders.get(subject, 1) - 1
        if self._holders[subject] <= 0:
            # Nobody is waiting or running for this caller; forget them, so the
            # dict tracks live users instead of everyone who ever connected.
            self._holders.pop(subject, None)
            self._semaphores.pop(subject, None)

    def in_flight(self, subject: str) -> int:
        """How many calls this caller currently has running or queued."""
        return self._holders.get(subject, 0)

    @asynccontextmanager
    async def slot(self, subject: str, *, tool_name: str = ""):
        """Hold one of the caller's slots for the duration of the block."""
        limit = max_concurrent()
        if limit <= 0:
            yield
            return

        semaphore = self._acquire_semaphore(subject, limit)
        timeout = queue_timeout_seconds()
        try:
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
            except TimeoutError as exc:
                logger.warning(
                    "Concurrency limit reached for subject=%s tool=%s (limit=%d)",
                    subject,
                    tool_name,
                    limit,
                )
                raise ConcurrencyLimitReached(
                    f"You already have {limit} queries running and the queue did not clear "
                    f"within {timeout:.0f}s. Wait for one to finish, or narrow the date range "
                    f"so it returns faster."
                ) from exc
            try:
                yield
            finally:
                semaphore.release()
        finally:
            self._release_semaphore(subject)


#: Process-wide limiter. One per replica; see the module docstring.
limiter = UserConcurrencyLimiter()
