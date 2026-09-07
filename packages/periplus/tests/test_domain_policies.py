import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nats.js.errors import KeyNotFoundError, KeyWrongLastSequenceError
from pydantic import ValidationError

from periplus.crawl.control.domain_policies.schemas import DomainPolicyCreateRequest
from periplus.crawl.control.domain_policies.service import DEFAULT_DOMAIN_POLICY_SLUG, find_domain_policy_for_url
from periplus.crawl.runtime.domain_pacing import (
    DomainCapacityUnavailable,
    DomainPacingState,
    domain_permit,
    domain_backoff_seconds,
    record_domain_response,
    try_domain_start,
)


def policy(slug: str, host_match: str):
    return SimpleNamespace(slug=slug, host_match=host_match, enabled=True)


class DomainPolicyResolutionTests(unittest.TestCase):
    def test_exact_then_wildcard_then_default_specificity(self):
        default = policy(DEFAULT_DOMAIN_POLICY_SLUG, "*")
        wildcard = policy("example-subdomains", "*.example.com")
        exact = policy("api", "api.example.com")
        session = MagicMock()
        session.scalars.return_value = [default, wildcard, exact]

        self.assertIs(find_domain_policy_for_url(session, url="https://api.example.com/a"), exact)
        self.assertIs(find_domain_policy_for_url(session, url="https://shop.example.com/a"), wildcard)
        self.assertIs(find_domain_policy_for_url(session, url="https://other.test/a"), default)

    def test_request_rejects_unsupported_wildcard_shapes(self):
        with self.assertRaises(ValidationError):
            DomainPolicyCreateRequest(
                slug="books",
                host_match="*books.toscrape.com*",
            )

    def test_request_normalizes_supported_host_patterns(self):
        exact = DomainPolicyCreateRequest(
            slug="books",
            host_match=" Books.ToScrape.Com ",
        )
        wildcard = DomainPolicyCreateRequest(
            slug="books-subdomains",
            host_match=" *.Books.ToScrape.Com ",
        )

        self.assertEqual(exact.host_match, "books.toscrape.com")
        self.assertEqual(wildcard.host_match, "*.books.toscrape.com")


class FakeBucket:
    def __init__(self):
        self.values = {}
        self.revisions = {}

    @property
    def value(self):
        return next(iter(self.values.values()), None)

    async def get(self, key):
        if key not in self.values:
            raise KeyNotFoundError
        return SimpleNamespace(
            value=self.values[key],
            revision=self.revisions[key],
        )

    async def create(self, key, value):
        if key in self.values:
            raise KeyWrongLastSequenceError
        self.values[key] = value
        self.revisions[key] = 1

    async def update(self, key, value, last):
        if last != self.revisions.get(key):
            raise KeyWrongLastSequenceError
        self.values[key] = value
        self.revisions[key] += 1


class DomainPacingTests(unittest.TestCase):
    def test_concurrency_is_scoped_to_the_domain_key(self):
        bucket = FakeBucket()

        async def scenario():
            first = domain_permit(
                bucket,
                domain="example.com",
                concurrency=1,
                acquire_timeout=0,
            )
            await first.__aenter__()
            try:
                with self.assertRaises(DomainCapacityUnavailable):
                    async with domain_permit(
                        bucket,
                        domain="example.com",
                        concurrency=1,
                        acquire_timeout=0,
                    ):
                        pass
                async with domain_permit(
                    bucket,
                    domain="other.example",
                    concurrency=1,
                    acquire_timeout=0,
                ):
                    pass
            finally:
                await first.__aexit__(None, None, None)
            async with domain_permit(
                bucket,
                domain="example.com",
                concurrency=1,
                acquire_timeout=0,
            ):
                pass

        asyncio.run(scenario())

    def test_updated_interval_uses_last_actual_start(self):
        async def scenario():
            bucket = FakeBucket()
            self.assertEqual(await try_domain_start(bucket, domain="example.com", interval_seconds=60), 0)
            self.assertGreater(await try_domain_start(bucket, domain="example.com", interval_seconds=60), 59)
            self.assertEqual(await try_domain_start(bucket, domain="example.com", interval_seconds=0), 0)
            self.assertGreater(await try_domain_start(bucket, domain="example.com", interval_seconds=120), 119)
        asyncio.run(scenario())

    def test_nonblocking_start_does_not_reserve_future_capacity(self):
        async def scenario():
            bucket = FakeBucket()
            with patch("periplus.crawl.runtime.domain_pacing.asyncio.sleep", new_callable=AsyncMock) as sleep:
                starts = await asyncio.gather(*(try_domain_start(bucket, domain="example.com", interval_seconds=60)
                                               for _ in range(8)))
                self.assertEqual(sum(delay == 0 for delay in starts), 1)
                self.assertTrue(all(59 < delay <= 60 for delay in starts if delay))
                self.assertEqual(next(iter(bucket.revisions.values())), 1)
                self.assertEqual(await try_domain_start(bucket, domain="other.example", interval_seconds=60), 0)
                await record_domain_response(bucket, domain="zero.example", status_code=429, retry_after_seconds=120)
                self.assertGreater(await try_domain_start(bucket, domain="zero.example", interval_seconds=0), 119)
                sleep.assert_not_awaited()
        asyncio.run(scenario())

    def test_429_applies_a_shared_retry_after_backoff(self):
        bucket = FakeBucket()

        async def scenario():
            delay = await record_domain_response(
                bucket,
                domain="example.com",
                status_code=429,
                retry_after_seconds=12,
            )
            shared_delay = await domain_backoff_seconds(
                bucket,
                domain="example.com",
            )
            self.assertGreaterEqual(delay, 11.9)
            self.assertGreaterEqual(shared_delay, 11.9)

        asyncio.run(scenario())

    def test_repeated_500s_trip_the_weaker_domain_backoff(self):
        bucket = FakeBucket()

        async def scenario():
            first = await record_domain_response(
                bucket, domain="example.com", status_code=500
            )
            second = await record_domain_response(
                bucket, domain="example.com", status_code=500
            )
            third = await record_domain_response(
                bucket, domain="example.com", status_code=500
            )
            state = DomainPacingState.model_validate_json(bucket.value)
            self.assertEqual(first, 0)
            self.assertEqual(second, 0)
            self.assertGreaterEqual(third, 0.9)
            self.assertEqual(state.transient_failure_count, 3)

        asyncio.run(scenario())

    def test_successful_responses_decay_the_failure_score(self):
        bucket = FakeBucket()

        async def scenario():
            await record_domain_response(
                bucket, domain="example.com", status_code=500
            )
            await record_domain_response(
                bucket, domain="example.com", status_code=200
            )
            state = DomainPacingState.model_validate_json(bucket.value)
            self.assertEqual(state.transient_failure_count, 0)
            self.assertIsNone(state.last_failure_at)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
