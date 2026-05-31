"""In-memory token-bucket throttle for the magic-link login endpoint.

Keyed by an arbitrary string (client IP + email) so a single client cannot
spam token generation / outbound mail. Mirrors the approach in
``soulservice.core.ratelimit`` but uses string keys and a single hourly window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from soulservice.core.config import settings


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class LoginThrottle:
    """Per-key hourly token bucket."""

    def __init__(self, per_hour: int | None = None):
        self.per_hour = per_hour or settings.web_login_rate_per_hour
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str) -> bool:
        """Consume one token for ``key``; return False when the bucket is empty."""
        capacity = float(self.per_hour)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=capacity)
            self._buckets[key] = bucket

        now = time.monotonic()
        elapsed = now - bucket.last_refill
        bucket.tokens = min(capacity, bucket.tokens + elapsed * (capacity / 3600.0))
        bucket.last_refill = now

        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True


login_throttle = LoginThrottle()
