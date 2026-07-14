from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock

from control.crawl_policies.service import (
    DEFAULT_POLICY_METRIC_SLUG,
    _matches,
    delete_crawl_policy,
    find_crawl_policies_for_urls,
)


def matcher(*, scheme: str, host: str, priority: int):
    return SimpleNamespace(
        scheme=scheme,
        host=host,
        path_pattern="/*",
        match_type="glob",
        priority=priority,
        enabled=True,
    )


class CrawlPolicyResolutionTests(TestCase):
    def test_wildcard_match_covers_http_and_https(self) -> None:
        catch_all = matcher(scheme="*", host="*", priority=-1_000_000)

        self.assertTrue(_matches("http://example.com/a", catch_all))
        self.assertTrue(_matches("https://another.example/b", catch_all))

    def test_specific_policy_overrides_seeded_catch_all(self) -> None:
        default = SimpleNamespace(
            metric_slug=DEFAULT_POLICY_METRIC_SLUG,
            url_match=matcher(scheme="*", host="*", priority=-1_000_000),
        )
        specific = SimpleNamespace(
            metric_slug="example",
            url_match=matcher(scheme="https", host="example.com", priority=0),
        )
        session = MagicMock()
        session.scalars.return_value = [default, specific]

        resolved = find_crawl_policies_for_urls(
            session, urls=["https://example.com/docs", "https://other.example/"]
        )

        self.assertIs(resolved["https://example.com/docs"], specific)
        self.assertIs(resolved["https://other.example/"], default)

    def test_missing_catch_all_fails_instead_of_using_an_implicit_profile(self) -> None:
        session = MagicMock()
        session.scalars.return_value = []

        with self.assertRaisesRegex(RuntimeError, "catch-all CrawlPolicy"):
            find_crawl_policies_for_urls(session, urls=["https://example.com/"])

    def test_default_policy_cannot_be_deleted(self) -> None:
        session = MagicMock()
        policy = SimpleNamespace(metric_slug=DEFAULT_POLICY_METRIC_SLUG)

        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            delete_crawl_policy(session, policy=policy)
        session.delete.assert_not_called()
