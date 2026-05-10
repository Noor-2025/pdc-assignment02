"""
circuit_breaker.py
==================
A lightweight async Circuit Breaker for protecting external service calls.

Design
------
The breaker tracks three states:

  CLOSED    All calls go through normally. Each failure increments a counter.
            Once the counter hits `failure_threshold`, the breaker trips to OPEN.

  OPEN      All calls are short-circuited immediately — no I/O occurs.
            A configurable `reset_after` cooldown begins when the breaker opens.

  HALF_OPEN After the cooldown expires, exactly one probe call is allowed.
            Success  → transitions back to CLOSED and clears the failure count.
            Failure  → immediately returns to OPEN and restarts the cooldown.

Author : Noor Fatima
Roll No: BSCS23166
Course : Parallel and Distributed Computing (PDC)
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum, auto
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ── State enumeration ──────────────────────────────────────────────────────────

class State(Enum):
    CLOSED    = auto()
    OPEN      = auto()
    HALF_OPEN = auto()


class BreakerTripError(Exception):
    """Raised when a call is attempted while the breaker is in OPEN state."""


# ── Core breaker ───────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Async-safe circuit breaker.

    Parameters
    ----------
    failure_threshold : int
        How many consecutive errors cause the breaker to open.
    reset_after : float
        Seconds to wait in OPEN before allowing a probe (HALF_OPEN).
    label : str
        A human-readable name shown in log messages and status output.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_after: float = 15.0,
        label: str = "unnamed",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_after = reset_after
        self.label = label

        self._state: State = State.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: Optional[float] = None
        self._mutex = asyncio.Lock()

    # ── Public interface ───────────────────────────────────────────────────────

    async def run(
        self,
        fn: Callable,
        /,
        *args: Any,
        fallback: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute `fn(*args, **kwargs)` through the breaker.

        If the breaker is OPEN, the call is bypassed entirely and `fallback`
        is returned (or BreakerTripError is raised when fallback is None).
        """
        async with self._mutex:
            self._maybe_try_reset()
            current = self._state

        if current is State.OPEN:
            log.warning("[%s] breaker is OPEN — skipping call", self.label)
            if fallback is not None:
                return fallback
            raise BreakerTripError(f"Breaker '{self.label}' is open; call blocked.")

        if current is State.HALF_OPEN:
            log.info("[%s] HALF_OPEN — sending probe request", self.label)

        try:
            result = await fn(*args, **kwargs)
            await self._record_success()
            return result

        except Exception as exc:
            await self._record_failure(exc)
            if fallback is not None:
                log.info("[%s] returning fallback after failure", self.label)
                return fallback
            raise

    def snapshot(self) -> dict:
        """Return a JSON-serialisable status snapshot."""
        cooldown_remaining: Optional[float] = None
        if self._state is State.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            cooldown_remaining = max(0.0, round(self.reset_after - elapsed, 1))

        return {
            "label": self.label,
            "state": self._state.name,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "reset_after_seconds": self.reset_after,
            "cooldown_remaining_seconds": cooldown_remaining,
        }

    def force_close(self) -> None:
        """Manually reset the breaker to CLOSED (useful in tests / admin routes)."""
        self._state = State.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        log.info("[%s] breaker forcibly closed", self.label)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _maybe_try_reset(self) -> None:
        """Transition OPEN → HALF_OPEN once the cooldown has elapsed."""
        if (
            self._state is State.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self.reset_after
        ):
            log.info("[%s] cooldown elapsed — moving to HALF_OPEN", self.label)
            self._state = State.HALF_OPEN

    async def _record_success(self) -> None:
        async with self._mutex:
            if self._state is State.HALF_OPEN:
                log.info("[%s] probe succeeded — returning to CLOSED", self.label)
            self._state = State.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None

    async def _record_failure(self, exc: Exception) -> None:
        async with self._mutex:
            self._consecutive_failures += 1
            self._opened_at = time.monotonic()
            log.warning(
                "[%s] failure #%d recorded: %s",
                self.label,
                self._consecutive_failures,
                exc,
            )
            if self._consecutive_failures >= self.failure_threshold:
                log.error(
                    "[%s] threshold hit (%d) — breaker now OPEN (cooldown %ss)",
                    self.label,
                    self.failure_threshold,
                    self.reset_after,
                )
                self._state = State.OPEN
