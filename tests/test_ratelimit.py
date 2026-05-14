"""Tests for the in-memory rate limiter."""

from __future__ import annotations

import time
from unittest.mock import patch
from uuid import uuid4

from soulservice.core.ratelimit import RateLimiter


class TestRateLimiter:
    def test_allows_first_request(self):
        rl = RateLimiter(per_minute=10, per_hour=100)
        token_id = uuid4()
        allowed, retry = rl.check(token_id)
        assert allowed is True
        assert retry == 0.0

    def test_allows_up_to_limit(self):
        rl = RateLimiter(per_minute=5, per_hour=1000)
        token_id = uuid4()
        for _ in range(5):
            allowed, _ = rl.check(token_id)
            assert allowed is True

    def test_blocks_after_minute_limit(self):
        rl = RateLimiter(per_minute=3, per_hour=1000)
        token_id = uuid4()
        for _ in range(3):
            rl.check(token_id)
        allowed, retry = rl.check(token_id)
        assert allowed is False
        assert retry > 0

    def test_blocks_after_hour_limit(self):
        rl = RateLimiter(per_minute=1000, per_hour=5)
        token_id = uuid4()
        for _ in range(5):
            rl.check(token_id)
        allowed, retry = rl.check(token_id)
        assert allowed is False
        assert retry > 0

    def test_different_tokens_independent(self):
        rl = RateLimiter(per_minute=2, per_hour=100)
        t1, t2 = uuid4(), uuid4()
        rl.check(t1)
        rl.check(t1)
        allowed_t1, _ = rl.check(t1)
        allowed_t2, _ = rl.check(t2)
        assert allowed_t1 is False
        assert allowed_t2 is True

    def test_refill_over_time(self):
        rl = RateLimiter(per_minute=60, per_hour=10000)
        token_id = uuid4()
        for _ in range(60):
            rl.check(token_id)
        allowed_before, _ = rl.check(token_id)
        assert allowed_before is False

        # Simulate time passing (1 second = 1 token refilled at 60/min)
        bucket = rl._minute_buckets[token_id]
        bucket.last_refill -= 2.0  # 2 seconds ago
        allowed_after, _ = rl.check(token_id)
        assert allowed_after is True

    def test_retry_after_is_reasonable(self):
        rl = RateLimiter(per_minute=60, per_hour=10000)
        token_id = uuid4()
        for _ in range(60):
            rl.check(token_id)
        _, retry = rl.check(token_id)
        assert 0 < retry <= 2.0
