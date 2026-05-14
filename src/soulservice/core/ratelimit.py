"""In-memory token bucket rate limiter."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from uuid import UUID

from soulservice.core.config import settings


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Per-token-id rate limiter with minute and hour windows."""

    def __init__(
        self,
        per_minute: int | None = None,
        per_hour: int | None = None,
    ):
        self.per_minute = per_minute or settings.rate_limit_per_minute
        self.per_hour = per_hour or settings.rate_limit_per_hour
        self._minute_buckets: dict[UUID, _Bucket] = {}
        self._hour_buckets: dict[UUID, _Bucket] = {}

    def _refill(self, bucket: _Bucket, capacity: int, interval: float) -> None:
        now = time.monotonic()
        elapsed = now - bucket.last_refill
        refill_rate = capacity / interval
        bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_rate)
        bucket.last_refill = now

    def check(self, token_id: UUID) -> tuple[bool, float]:
        """Check if a request is allowed.

        Returns (allowed, retry_after_seconds).
        """
        if token_id not in self._minute_buckets:
            self._minute_buckets[token_id] = _Bucket(tokens=float(self.per_minute))
        if token_id not in self._hour_buckets:
            self._hour_buckets[token_id] = _Bucket(tokens=float(self.per_hour))

        mb = self._minute_buckets[token_id]
        hb = self._hour_buckets[token_id]

        self._refill(mb, self.per_minute, 60.0)
        self._refill(hb, self.per_hour, 3600.0)

        if mb.tokens < 1:
            retry = (1 - mb.tokens) / (self.per_minute / 60.0)
            return False, retry
        if hb.tokens < 1:
            retry = (1 - hb.tokens) / (self.per_hour / 3600.0)
            return False, retry

        mb.tokens -= 1
        hb.tokens -= 1
        return True, 0.0


rate_limiter = RateLimiter()
