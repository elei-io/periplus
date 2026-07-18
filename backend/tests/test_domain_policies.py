import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from nats.js.errors import KeyNotFoundError, KeyWrongLastSequenceError
from pydantic import ValidationError

from control.domain_policies.schemas import DomainPolicyCreateRequest
from control.domain_policies.service import DEFAULT_DOMAIN_POLICY_SLUG, find_domain_policy_for_url
from runtime.domain_pacing import wait_for_domain_interval


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
        self.value = None
        self.revision = 0

    async def get(self, _key):
        if self.value is None:
            raise KeyNotFoundError
        return SimpleNamespace(value=self.value, revision=self.revision)

    async def create(self, _key, value):
        if self.value is not None:
            raise KeyWrongLastSequenceError
        self.value = value
        self.revision = 1

    async def update(self, _key, value, last):
        if last != self.revision:
            raise KeyWrongLastSequenceError
        self.value = value
        self.revision += 1


class DomainPacingTests(unittest.TestCase):
    def test_reservations_serialize_the_minimum_interval(self):
        bucket = FakeBucket()

        async def scenario():
            with patch("runtime.domain_pacing.asyncio.sleep", new_callable=AsyncMock) as sleep:
                await wait_for_domain_interval(bucket, domain="example.com", interval_seconds=2)
                await wait_for_domain_interval(bucket, domain="example.com", interval_seconds=2)
            self.assertEqual(sleep.await_count, 1)
            self.assertGreaterEqual(sleep.await_args.args[0], 1.9)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
