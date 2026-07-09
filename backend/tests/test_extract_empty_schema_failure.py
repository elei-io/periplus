from unittest import TestCase

from actions.crawl.schemas import CrawlPage
from actions.extract.service import _clean_empty_schema_error
from actions.shared.quality.schemas import QualityWarning


def _warning(code: str) -> QualityWarning:
    return QualityWarning(code=code, name=code, description=code)


class ExtractEmptySchemaFailureTests(TestCase):
    def test_clean_empty_extraction_is_schema_error(self) -> None:
        page = CrawlPage(
            url="https://example.com/item/1",
            success=True,
            status_code=200,
            duration_seconds=0.1,
            html="<html><body>Item</body></html>",
            crawl={"success": True},
        )

        self.assertIsNotNone(_clean_empty_schema_error(page, [_warning("empty_extraction")]))

    def test_page_warning_prevents_schema_error(self) -> None:
        page = CrawlPage(
            url="https://example.com/item/1",
            success=True,
            status_code=200,
            duration_seconds=0.1,
            html="<html></html>",
            crawl={"success": True},
            warnings=[_warning("app_shell")],
        )

        self.assertIsNone(_clean_empty_schema_error(page, [_warning("empty_extraction")]))

    def test_quality_warning_prevents_schema_error(self) -> None:
        page = CrawlPage(
            url="https://example.com/item/1",
            success=True,
            status_code=200,
            duration_seconds=0.1,
            html="<html></html>",
            crawl={"success": True},
        )

        self.assertIsNone(
            _clean_empty_schema_error(page, [_warning("empty_extraction"), _warning("lazy_load")])
        )

    def test_crawl_error_prevents_schema_error(self) -> None:
        page = CrawlPage(
            url="https://example.com/item/1",
            success=True,
            status_code=200,
            duration_seconds=0.1,
            html="<html></html>",
            crawl={"success": False, "error_message": "blocked"},
        )

        self.assertIsNone(_clean_empty_schema_error(page, [_warning("empty_extraction")]))
