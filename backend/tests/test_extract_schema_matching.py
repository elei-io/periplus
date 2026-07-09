from types import SimpleNamespace
from unittest import TestCase

from extract_schemas.service import extract_schema_matches_url, match_targets_for_url


class ExtractSchemaMatchingTests(TestCase):
    def test_glob_match_applies_to_query_stripped_url(self) -> None:
        schema = SimpleNamespace(match="*example.com/item/*")

        self.assertTrue(extract_schema_matches_url(schema, "https://example.com/item/123?ref=search"))

    def test_query_pattern_applies_to_full_url_without_fragment(self) -> None:
        schema = SimpleNamespace(match="https://example.com/search?q*")

        self.assertTrue(extract_schema_matches_url(schema, "https://example.com/search?q=drives#results"))

    def test_non_matching_pattern_is_rejected(self) -> None:
        schema = SimpleNamespace(match="*example.com/item/*")

        self.assertFalse(extract_schema_matches_url(schema, "https://example.com/search?q=drives"))

    def test_match_targets_include_full_and_query_stripped_forms(self) -> None:
        self.assertEqual(
            match_targets_for_url("https://example.com/search?q=drives#results"),
            (
                "https://example.com/search?q=drives",
                "https://example.com/search",
            ),
        )
