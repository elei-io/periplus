from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock

from atlas.crawl.control.crawl_policies.service import (
    DEFAULT_POLICY_SLUG,
    _matches,
    default_content_policy,
    delete_crawl_policy,
    ensure_default_crawl_policy,
    find_crawl_policies_for_urls,
    update_crawl_policy,
)
from atlas.crawl.control.crawl_policies.schemas import CrawlPolicyCreateRequest


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
    session.scalars.return_value = list(policies)
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

    def test_subdomain_wildcard_matches_subdomains_but_not_root_domain(self) -> None:
        wikipedia = policy(slug="wikipedia", host="*.wikipedia.org")

        self.assertTrue(_matches("https://en.wikipedia.org/wiki/Atlas", wikipedia))
        self.assertTrue(
            _matches("https://en.m.wikipedia.org/wiki/Atlas", wikipedia)
        )
        self.assertFalse(_matches("https://wikipedia.org/", wikipedia))
        self.assertFalse(_matches("https://notwikipedia.org/", wikipedia))
        self.assertTrue(
            _matches("https://en.wikipedia.org:8443/wiki/Atlas", wikipedia)
        )

    def test_exact_host_outranks_subdomain_wildcard(self) -> None:
        default = policy(slug=DEFAULT_POLICY_SLUG)
        wikipedia = policy(slug="wikipedia", host="*.wikipedia.org")
        english = policy(slug="english-wikipedia", host="en.wikipedia.org")

        resolved = find_crawl_policies_for_urls(
            session_with(default, wikipedia, english),
            urls=[
                "https://en.wikipedia.org/wiki/Atlas",
                "https://de.wikipedia.org/wiki/Atlas",
            ],
        )

        self.assertIs(resolved["https://en.wikipedia.org/wiki/Atlas"], english)
        self.assertIs(resolved["https://de.wikipedia.org/wiki/Atlas"], wikipedia)

    def test_more_specific_subdomain_wildcard_wins(self) -> None:
        default = policy(slug=DEFAULT_POLICY_SLUG)
        org = policy(slug="org", host="*.org")
        wikipedia = policy(slug="wikipedia", host="*.wikipedia.org")

        resolved = find_crawl_policies_for_urls(
            session_with(default, org, wikipedia),
            urls=["https://en.wikipedia.org/wiki/Atlas"],
        )

        self.assertIs(resolved["https://en.wikipedia.org/wiki/Atlas"], wikipedia)

    def test_request_accepts_only_supported_host_wildcards(self) -> None:
        request = CrawlPolicyCreateRequest(
            slug="wikipedia",
            scheme="*",
            host=" *.Wikipedia.org ",
        )

        self.assertEqual(request.host, "*.wikipedia.org")
        with self.assertRaisesRegex(ValueError, r"\*\.domain wildcard"):
            CrawlPolicyCreateRequest(
                slug="invalid",
                scheme="*",
                host="wiki*.wikipedia.org",
            )

    def test_missing_catch_all_fails_instead_of_using_an_implicit_profile(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "catch-all CrawlPolicy"):
            find_crawl_policies_for_urls(
                session_with(), urls=["https://example.com/"]
            )

    def test_default_policy_cannot_be_deleted(self) -> None:
        session = MagicMock()
        default = SimpleNamespace(slug=DEFAULT_POLICY_SLUG)

        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            delete_crawl_policy(session, policy=default)
        session.delete.assert_not_called()

    def test_setup_creates_the_canonical_default_content_policy(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None

        default = ensure_default_crawl_policy(session)

        self.assertEqual(default.slug, DEFAULT_POLICY_SLUG)
        self.assertEqual(
            (default.scheme, default.host, default.path_prefix, default.path_mode),
            ("*", "*", "/", "prefix"),
        )
        self.assertTrue(default.enabled)
        self.assertEqual(
            default.content,
            default_content_policy().model_dump(mode="json"),
        )
        self.assertTrue(default.content["completion"]["wait_dynamic"]["enabled"])
        self.assertFalse(default.content["completion"]["wait_fixed"]["enabled"])
        self.assertTrue(default.content["completion"]["scroll"]["enabled"])
        self.assertTrue(default.content["completion"]["expand"]["enabled"])
        session.add.assert_called_once_with(default)
        session.flush.assert_called_once_with()

    def test_setup_preserves_user_edits_to_the_default_content_policy(self) -> None:
        stored = SimpleNamespace(
            slug=DEFAULT_POLICY_SLUG,
            scheme="https",
            host="example.com",
            path_prefix="/docs",
            path_mode="exact",
            content={"completion": {}},
            enabled=False,
            updated_at=None,
        )
        session = MagicMock()
        session.scalar.return_value = stored

        preserved = ensure_default_crawl_policy(session)

        self.assertIs(preserved, stored)
        self.assertEqual(
            (stored.scheme, stored.host, stored.path_prefix, stored.path_mode),
            ("https", "example.com", "/docs", "exact"),
        )
        self.assertFalse(stored.enabled)
        self.assertEqual(stored.content, {"completion": {}})
        self.assertIsNone(stored.updated_at)
        session.add.assert_not_called()
        session.flush.assert_not_called()

    def test_default_policy_content_can_be_changed(self) -> None:
        stored = SimpleNamespace(
            id="policy-id",
            slug=DEFAULT_POLICY_SLUG,
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content=default_content_policy().model_dump(mode="json"),
            enabled=True,
            updated_at=None,
        )
        session = MagicMock()
        session.scalar.return_value = stored
        weakened = default_content_policy().model_dump(mode="json")
        weakened["completion"]["scroll"]["enabled"] = False

        updated = update_crawl_policy(session, policy=stored, content=weakened)

        self.assertFalse(updated.content["completion"]["scroll"]["enabled"])
        session.flush.assert_called_once_with()

    def test_default_policy_match_and_enabled_state_cannot_be_changed(self) -> None:
        stored = SimpleNamespace(
            id="policy-id",
            slug=DEFAULT_POLICY_SLUG,
            scheme="*",
            host="*",
            path_prefix="/",
            path_mode="prefix",
            content=default_content_policy().model_dump(mode="json"),
            enabled=True,
            updated_at=None,
        )
        session = MagicMock()
        session.scalar.return_value = stored

        with self.assertRaisesRegex(ValueError, "match every URL"):
            update_crawl_policy(session, policy=stored, host="example.com")
        with self.assertRaisesRegex(ValueError, "cannot be disabled"):
            update_crawl_policy(session, policy=stored, enabled=False)

    def test_update_accepts_serialized_content_from_api_payload(self) -> None:
        stored = SimpleNamespace(
            id="policy-id",
            slug="example",
            scheme="https",
            host="example.com",
            path_prefix="/",
            path_mode="prefix",
            content={},
            enabled=True,
            updated_at=None,
        )
        session = MagicMock()
        session.scalar.return_value = stored

        updated = update_crawl_policy(
            session,
            policy=stored,
            content={
                "accepted_content_types": ["text/html"],
                "response_rules": {
                    "http_status": [
                        {"minimum": 429, "maximum": 429, "outcome": "retry"}
                    ],
                    "unsupported_content_type": "skip",
                },
                "completion": {
                    "navigation": {
                        "timeout_ms": 30_000,
                        "context_replacement_retries": 2,
                        "context_replacement_settle_ms": 1_000,
                    },
                    "wait_dynamic": {
                        "enabled": True,
                        "maximum_wait_ms": 8_000,
                        "sample_interval_ms": 250,
                        "stable_samples": 3,
                    },
                    "wait_fixed": {"enabled": False, "duration_ms": 0},
                    "scroll": {
                        "enabled": False,
                        "maximum_iterations": 30,
                        "viewport_ratio": 0.85,
                        "wait_ms": 250,
                        "stable_bottom_samples": 3,
                    },
                    "expand": {
                        "enabled": True,
                        "maximum_actions": 10,
                        "wait_ms": 500,
                    },
                },
            },
        )

        self.assertIs(updated, stored)
        self.assertEqual(updated.content["accepted_content_types"], ["text/html"])
        self.assertFalse(updated.content["completion"]["scroll"]["enabled"])
        session.flush.assert_called_once_with()
