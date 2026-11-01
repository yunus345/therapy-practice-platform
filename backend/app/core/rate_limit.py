"""Per-route rate limiting.

`InMemoryRateLimitBackend` keeps its state in one process. With several workers each
one counts separately, and a restart clears all counters. A shared backend can replace
it by implementing `RateLimitBackend`.
"""

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from app.core.errors import RateLimitedError


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


class RateLimitBackend(Protocol):
    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult: ...


class InMemoryRateLimitBackend:
    """Sliding window log. Rejected attempts do not extend the window."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, tuple[int, deque[float]]] = {}
        self._next_sweep_at = 0.0

    async def hit(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = self._clock()
        self._sweep(now, window_seconds)

        _, hits = self._hits.setdefault(key, (window_seconds, deque()))
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()

        if len(hits) >= limit:
            retry_after = math.ceil(hits[0] + window_seconds - now)
            return RateLimitResult(allowed=False, retry_after_seconds=max(retry_after, 1))

        hits.append(now)
        return RateLimitResult(allowed=True, retry_after_seconds=0)

    def _sweep(self, now: float, window_seconds: int) -> None:
        if now < self._next_sweep_at:
            return
        self._next_sweep_at = now + window_seconds
        self._hits = {
            key: (window, hits)
            for key, (window, hits) in self._hits.items()
            if hits and hits[-1] > now - window
        }


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class RateLimit:
    """Route dependency: `dependencies=[Depends(RateLimit("login", 5, 60))]`."""

    def __init__(
        self,
        scope: str,
        limit: int,
        window_seconds: int,
        key: Callable[[Request], str] = client_ip,
    ) -> None:
        self.scope = scope
        self.limit = limit
        self.window_seconds = window_seconds
        self.key = key

    async def __call__(self, request: Request) -> None:
        backend: RateLimitBackend = request.app.state.rate_limit_backend
        result = await backend.hit(
            f"{self.scope}:{self.key(request)}", self.limit, self.window_seconds
        )
        if not result.allowed:
            raise RateLimitedError(
                details={"retry_after_seconds": result.retry_after_seconds},
                headers={"Retry-After": str(result.retry_after_seconds)},
            )
