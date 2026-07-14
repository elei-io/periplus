from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock

from control.crawl_policies.service import (
    DEFAULT_POLICY_SLUG,
    _matches,
    delete_crawl_policy,
    find_crawl_policies_for_urls,
)


def policy(
    *,
    slug: str,
    scheme: str = "*",
    host: str = "*",
    path_prefix: str = "/",
    path_mode: str = "prefix",
):
    return SimpleNamespace(
        slug=slug,
        scheme=scheme,
        host=host,
        path_prefix=path_prefix,
        path_mode=path_mode,
        enabled=True,
    )


def session_with(*policies):
    session = MagicMock()
    result = MagicMock()
    result.unique.return_value = list(policies)
    session.scalars.return_value = result
    return session


class CrawlPolicyResolutionTests(TestCase):
    def test_wildcard_match_covers_http_and_https(self) -> None:
        catch_all = policy(slug=DEFAULT_POLICY_SLUG)

        self.assertTrue(_matches("http://example.com/a", catch_all))
        self.assertTrue(_matches("https://another.example/b", catch_all))

    def test_host_and_path_specificity_override_catch_all(self) -> None:
        default = policy(slug=DEFAULT_POLICY_SLUG)
        site = policy(slug="example", scheme="https", host="example.com")
        docs = policy(
            slug="docs",
            scheme="https",
            host="example.com",
            path_prefix="/docs",
        )
        exact = policy(
            slug="docs-index",
            scheme="https",
            host="example.com",
            path_prefix="/docs",
            path_mode="exact",
        )

        resolved = find_crawl_policies_for_urls(
            session_with(default, site, docs, exact),
            urls=[
                "https://example.com/docs",
                "https://example.com/docs/api",
                "https://example.com/about",
                "https://other.example/",
            ],
        )

        self.assertIs(resolved["https://example.com/docs"], exact)
        self.assertIs(resolved["https://example.com/docs/api"], docs)
        self.assertIs(resolved["https://example.com/about"], site)
        self.assertIs(resolved["https://other.example/"], default)

    def test_missing_catch_all_fails_instead_of_using_an_implicit_profile(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "catch-all CrawlPolicy"):
            find_crawl_policies_for_urls(
                session_with(), urls=["https://example.com/"]
            )

    def test_default_policy_cannot_be_deleted(self) -> None:
        session = MagicMock()

        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            delete_crawl_policy(
                session, policy=SimpleNamespace(slug=DEFAULT_POLICY_SLUG)
            )
        session.delete.assert_not_called()
