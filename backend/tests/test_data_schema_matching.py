from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from actions.crawl.schemas import CrawlOutput, CrawlPage, CrawlStats
from actions.shared.data_schema.service import _crawl_html
from control.data_schemas.service import (
    create_data_schema,
    data_schema_matches_url,
    match_targets_for_url,
)


class DataSchemaMatchingTests(TestCase):
    def test_generated_schema_records_source_crawl_and_document(self) -> None:
        crawl_id = uuid4()
        document_id = "sha256:" + "b" * 64
        session = MagicMock()
        session.scalar.return_value = None

        schema = create_data_schema(
            session,
            url="https://example.com/item/1",
            prompt="Extract the title",
            schema_type="css",
            target_json_example='{"title":"Atlas"}',
            match="*example.com/item/*",
            schema_json={"baseSelector": "body", "fields": []},
            task_run_id=uuid4(),
            crawl_id=crawl_id,
            document_id=document_id,
        )

        self.assertEqual(schema.generated_from_crawl_id, crawl_id)
        self.assertEqual(schema.generated_from_document_id, document_id)

    def test_glob_match_applies_to_query_stripped_url(self) -> None:
        schema = SimpleNamespace(match="*example.com/item/*")

        self.assertTrue(data_schema_matches_url(schema, "https://example.com/item/123?ref=search"))

    def test_query_pattern_applies_to_full_url_without_fragment(self) -> None:
        schema = SimpleNamespace(match="https://example.com/search?q*")

        self.assertTrue(data_schema_matches_url(schema, "https://example.com/search?q=drives#results"))

    def test_non_matching_pattern_is_rejected(self) -> None:
        schema = SimpleNamespace(match="*example.com/item/*")

        self.assertFalse(data_schema_matches_url(schema, "https://example.com/search?q=drives"))

    def test_match_targets_include_full_and_query_stripped_forms(self) -> None:
        self.assertEqual(
            match_targets_for_url("https://example.com/search?q=drives#results"),
            (
                "https://example.com/search?q=drives",
                "https://example.com/search",
            ),
        )


class DataSchemaCrawlTests(IsolatedAsyncioTestCase):
    async def test_crawl_html_returns_durable_source_identity(self) -> None:
        crawl_id = uuid4()
        document_id = "sha256:" + "c" * 64
        output = CrawlOutput(
            stats=CrawlStats(
                requested_urls=1,
                succeeded=1,
                failed=0,
                duration_seconds=0.1,
            ),
            pages=[
                CrawlPage(
                    url="https://example.com",
                    success=True,
                    duration_seconds=0.1,
                    html="<html></html>",
                    crawl_id=crawl_id,
                    document_id=document_id,
                )
            ],
        )
        with patch(
            "actions.shared.data_schema.service.crawl_service",
            new=AsyncMock(return_value=output),
        ):
            result = await _crawl_html(
                "https://example.com",
                None,
                None,
                None,
                None,
            )

        self.assertEqual(result, ("<html></html>", crawl_id, document_id))
