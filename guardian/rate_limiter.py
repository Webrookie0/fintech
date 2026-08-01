"""Shared sliding-window rate limiter for LLM API calls.

Stdlib only. Both PlannerAgent and Judge reuse a single module-level
limiter instance so every outbound LLM request in a process shares the
same 60-second window.

Configuration (read at construction):
    LLM_RATE_LIMIT_RPM   - requests per minute, default 15
    LLM_RATE_LIMIT_MODE  - "wait" (block until a slot frees) or
                           "fail" (raise RuntimeError), default "wait"
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque


class RateLimiter:
    """Sliding-window request throttle.

    Call wait() immediately before every HTTP request. It records the
    request's timestamp if a slot is free; otherwise it either blocks
    until the oldest recorded request leaves the window (wait mode) or
    raises RuntimeError (fail mode). Failed/blocked requests are never
    recorded, so they do not consume capacity.
    """

    def __init__(
        self,
        rpm: int | None = None,
        mode: str | None = None,
        window_seconds: float = 60.0,
    ) -> None:
        if rpm is None:
            rpm = int(os.environ.get("LLM_RATE_LIMIT_RPM", "15"))
        if mode is None:
            mode = os.environ.get("LLM_RATE_LIMIT_MODE", "wait")
        if mode not in ("wait", "fail"):
            raise ValueError(f"invalid rate limiter mode: {mode!r} (expected 'wait' or 'fail')")

        self.rpm = rpm
        self.mode = mode
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block (wait mode) or raise (fail mode) until a slot is free."""
        while True:
            with self._lock:
                now = time.monotonic()
                while (
                    self._timestamps
                    and now - self._timestamps[0] >= self.window_seconds
                ):
                    self._timestamps.popleft()
                if self.rpm <= 0 or len(self._timestamps) < self.rpm:
                    self._timestamps.append(now)
                    return
                oldest = self._timestamps[0]
                wait_seconds = self.window_seconds - (now - oldest)

            # Sleep outside the lock so other threads can keep pruning.
            if self.mode == "fail":
                raise RuntimeError(
                    f"Rate limit exceeded ({self.rpm} requests/minute)."
                )
            print(f"[RateLimiter] Limit reached ({self.rpm} RPM).")
            print(f"Waiting {wait_seconds:.1f} seconds...")
            time.sleep(wait_seconds)
            print("[RateLimiter] Resuming requests.")


_default: RateLimiter | None = None


def default_limiter() -> RateLimiter:
    """Return the single shared limiter for the whole process.

    PlannerAgent and Judge both call this, so all LLM requests share
    one window and one request budget.
    """
    global _default
    if _default is None:
        _default = RateLimiter()
    return _default
