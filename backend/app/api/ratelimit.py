"""A small in-process sliding-window limiter for starting runs (each run costs many model calls).

It is per process: fine for a single-instance demo deployment. Multiple instances would need a shared store.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.api.errors import ApiError
from app.config import get_settings


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class SlidingWindowLimiter:
    def __init__(self, window_seconds: int = 3600):
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, request: Request) -> None:
        limit = get_settings().runs_per_hour_per_client
        key = client_key(request)
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= limit:
                raise ApiError(429, "rate_limited", f"Research run limit reached ({limit} per hour). Try again later.")
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


run_limiter = SlidingWindowLimiter()
