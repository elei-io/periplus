from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from ducklake_client import DiskStorage, DuckDBCatalog

from catalogue import (
    Catalogue,
    CatalogueBatchEntry,
    CatalogueConfig,
    CatalogueConflictError,
    CatalogueService,
    CatalogueValidationError,
    CrawlRecord,
    DocumentRecord,
    ElementRecord,
    RunCrawlUsageRecord,
    RunManifestRecord,
)
from catalogue.benchmark import run_hot_path_benchmark

DOCUMENT_ID = "sha256:" + "a" * 64
HTML_SHA256 = "a" * 64
CRAWL_ID = UUID("00000000-0000-0000-0000-000000000001")


class CatalogueServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.catalogue = Catalogue(
            CatalogueConfig(
                catalog=DuckDBCatalog(root / "catalog.ducklake"),
                storage=DiskStorage(root / "lake"),
            )
        )
        self.catalogue.bootstrap()
        self.service = CatalogueService(self.catalogue)

    def tearDown(self) -> None:
        self.catalogue.close()
        self.temp_dir.cleanup()

    def test_record_crawl_is_atomic_queryable_and_idempotent(self) -> None:
        document = _document()
        crawl = _crawl()
        elements = _elements()

        created = self.service.record_crawl(
            document=document,
            crawl=crawl,
            elements=elements,
        )
        retried = self.service.record_crawl(
            document=document,
            crawl=crawl,
            elements=elements,
        )

        self.assertTrue(created.document_created)
        self.assertTrue(created.crawl_created)
        self.assertFalse(retried.document_created)
        self.assertFalse(retried.crawl_created)
        self.assertGreaterEqual(retried.repository_snapshot, created.repository_snapshot)
        self.assertEqual(self.service.get_document(DOCUMENT_ID), document)
        self.assertEqual(self.service.get_crawl(CRAWL_ID), crawl)
        self.assertEqual(self.service.get_elements(DOCUMENT_ID), elements)
        self.assertEqual(self._count("documents"), 1)
        self.assertEqual(self._count("crawls"), 1)
        self.assertEqual(self._count("elements"), 4)

    def test_get_elements_pages_in_document_order(self) -> None:
        self._record()

        page = self.service.get_elements(DOCUMENT_ID, limit=2, offset=1)

        self.assertEqual([element.element_index for element in page], [1, 2])

    def test_cached_crawl_usage_is_durable_queryable_and_idempotent(self) -> None:
        self._record()
        run_id = UUID("00000000-0000-0000-0000-000000000099")
        manifest = RunManifestRecord(
            run_id=run_id,
            task_id=UUID("00000000-0000-0000-0000-000000000098"),
            task_revision=1,
            primitive="index",
            input_json={"url": "https://example.com/start"},
            queued_at=datetime(2026, 7, 10, 12, 2, tzinfo=UTC),
        )
        usage = RunCrawlUsageRecord(
            usage_id=UUID("00000000-0000-0000-0000-000000000097"),
            run_id=run_id,
            crawl_id=CRAWL_ID,
            document_id=DOCUMENT_ID,
            requested_url="https://example.com/start",
            normalized_url="https://example.com/start",
            source="repository",
            role="primitive_result",
            ordinal=0,
            returned=True,
        )
        entry = CatalogueBatchEntry(
            document=_document(),
            crawl=_crawl(),
            run_usage=usage,
        )

        self.service.record_run_manifest(manifest)
        self.service.record_run_manifest(manifest)
        second_usage = usage.model_copy(
            update={"usage_id": UUID("00000000-0000-0000-0000-000000000096")}
        )
        with patch.object(
            self.service,
            "_fetch_rows",
            wraps=self.service._fetch_rows,
        ) as fetch_rows:
            self.service.record_crawl_batch(
                [entry, replace(entry, run_usage=second_usage)]
            )
        self.assertEqual(fetch_rows.call_count, 4)
        self.service.record_crawl_batch([entry])

        self.assertTrue(self.service.has_run_usage(run_id))
        self.assertEqual(self.service.get_run_manifest(run_id), manifest)
        self.assertEqual(
            self.service.get_run_usages(run_id),
            [second_usage, usage],
        )
        self.assertEqual(self._count("run_manifests"), 1)
        self.assertEqual(self._count("run_crawl_usages"), 2)

    def test_cached_crawls_are_filtered_by_capture_window(self) -> None:
        document = _document()
        first_at = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
        second_at = first_at + timedelta(minutes=10)
        self.service.record_crawl(
            document=document,
            crawl=_crawl(captured_at=first_at),
            elements=_elements(),
        )
        self.service.record_crawl(
            document=document,
            crawl=_crawl(
                crawl_id=UUID("00000000-0000-0000-0000-000000000002"),
                captured_at=second_at,
            ),
            elements=_elements(),
        )

        fresh = self.service.find_cached_crawls(
            normalized_url="https://example.com/start",
            input_hash="input:v1",
            captured_after=second_at,
        )
        stale = self.service.find_cached_crawls(
            normalized_url="https://example.com/start",
            input_hash="input:v1",
            captured_after=first_at,
            captured_before=second_at,
        )

        self.assertEqual([crawl.crawl_id for crawl in fresh], [UUID(int=2)])
        self.assertEqual([crawl.crawl_id for crawl in stale], [CRAWL_ID])

    def test_documentless_failure_is_durable_but_never_cache_eligible(self) -> None:
        failed = _crawl().model_copy(
            update={
                "document_id": None,
                "status_code": None,
                "errors_json": [{"message": "DNS lookup failed"}],
            }
        )

        result = self.service.record_crawl_batch(
            [CatalogueBatchEntry(document=None, crawl=failed)]
        )[0]

        self.assertIsNone(result.document_id)
        self.assertFalse(result.document_created)
        self.assertTrue(result.crawl_created)
        self.assertEqual(self.service.get_crawl(failed.crawl_id), failed)
        self.assertEqual(
            self.service.find_cached_crawls(
                normalized_url=failed.normalized_url,
                input_hash=failed.input_hash,
            ),
            [],
        )
        health = self.service.crawl_health_since(
            datetime(2026, 7, 10, 11, 0, tzinfo=UTC)
        )
        self.assertEqual(int(health["summary"]["total"]), 1)
        self.assertEqual(int(health["summary"]["succeeded"]), 0)
        self.assertEqual(health["failures"][0]["reason"], "dns")

    def test_documentless_crawl_without_an_error_is_rejected(self) -> None:
        invalid = _crawl().model_copy(
            update={"document_id": None, "errors_json": []}
        )

        with self.assertRaisesRegex(
            CatalogueValidationError,
            "must record an acquisition error",
        ):
            self.service.record_crawl_batch(
                [CatalogueBatchEntry(document=None, crawl=invalid)]
            )

    def test_get_links_returns_only_raw_anchor_hrefs_in_document_order(self) -> None:
        self._record()

        links = self.service.get_links(DOCUMENT_ID)

        self.assertEqual(
            [(link.element_index, link.href, link.text) for link in links],
            [(1, "/first", "First")],
        )

    def test_hot_path_benchmark_is_bounded_by_its_sample_size(self) -> None:
        self._record()

        result = run_hot_path_benchmark(self.catalogue, samples=1)

        self.assertEqual(result["samples"], {"documents": 1, "crawls": 1, "links": 1})
        self.assertEqual(result["document_batch_lookup"]["requested"], 1)
        self.assertEqual(result["document_batch_lookup"]["found"], 1)
        self.assertEqual(result["cache_lookup"]["count"], 1)
        self.assertEqual(result["element_page_100"]["count"], 1)
        self.assertEqual(result["link_projection"]["count"], 1)

    def test_conflicting_crawl_rolls_back_a_new_document(self) -> None:
        self._record()
        second_document = _document(
            document_id="sha256:" + "b" * 64,
            html_sha256="b" * 64,
        )
        conflicting_crawl = _crawl(document_id=second_document.document_id)

        with self.assertRaises(CatalogueConflictError):
            self.service.record_crawl(
                document=second_document,
                crawl=conflicting_crawl,
                elements=_elements(),
            )

        self.assertIsNone(self.service.get_document(second_document.document_id))
        self.assertEqual(self._count("documents"), 1)
        self.assertEqual(self._count("elements"), 4)

    def test_document_identity_must_be_derived_from_its_content_hash(self) -> None:
        self._record()
        duplicate = _document(document_id="alternate-document")

        with self.assertRaisesRegex(
            CatalogueValidationError,
            "sha256-prefixed canonical HTML hash",
        ):
            self.service.record_crawl(
                document=duplicate,
                crawl=_crawl(
                    crawl_id=UUID("00000000-0000-0000-0000-000000000002"),
                    document_id=duplicate.document_id,
                ),
                elements=_elements(),
            )

        self.assertIsNone(self.service.get_document(duplicate.document_id))

    def test_invalid_element_batch_is_rejected_before_writing(self) -> None:
        elements = list(_elements())
        elements[1] = elements[1].model_copy(update={"element_index": 7})

        with self.assertRaises(CatalogueValidationError):
            self.service.record_crawl(
                document=_document(),
                crawl=_crawl(),
                elements=elements,
            )

        self.assertEqual(self._count("documents"), 0)
        self.assertEqual(self._count("crawls"), 0)
        self.assertEqual(self._count("elements"), 0)

    def test_absolute_object_key_is_rejected_before_writing(self) -> None:
        document = _document().model_copy(update={"html_object_key": "/private/raw.html.zst"})

        with self.assertRaises(CatalogueValidationError):
            self.service.record_crawl(
                document=document,
                crawl=_crawl(),
                elements=_elements(),
            )

        self.assertEqual(self._count("documents"), 0)

    def test_invalid_pagination_is_rejected(self) -> None:
        with self.assertRaises(CatalogueValidationError):
            self.service.get_elements(DOCUMENT_ID, limit=0)
        with self.assertRaises(CatalogueValidationError):
            self.service.get_elements(DOCUMENT_ID, offset=-1)

    def _record(self) -> None:
        self.service.record_crawl(
            document=_document(),
            crawl=_crawl(),
            elements=_elements(),
        )

    def _count(self, table: str) -> int:
        value = self.catalogue.lake.sql_scalar(f"SELECT count(*) FROM atlas.main.{table}")
        assert isinstance(value, int)
        return value


def _document(
    *,
    document_id: str = DOCUMENT_ID,
    html_sha256: str = HTML_SHA256,
) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        html_sha256=html_sha256,
        html_object_key=f"raw/html/sha256/{html_sha256[:2]}/{html_sha256[2:4]}/{html_sha256}.html.zst",
        html_content_type="text/html",
        html_encoding="utf-8",
        html_size_bytes=100,
        html_compressed_size_bytes=50,
        compression="zstd",
        dom_schema_version=1,
        parser_name="lxml",
        parser_version="6.0.0",
        parser_options_hash="options:v1",
        element_count=4,
        created_at=datetime(2026, 7, 10, 12, 0, tzinfo=UTC),
    )


def _crawl(
    *,
    crawl_id: UUID = CRAWL_ID,
    document_id: str = DOCUMENT_ID,
    captured_at: datetime | None = None,
) -> CrawlRecord:
    return CrawlRecord(
        crawl_id=crawl_id,
        document_id=document_id,
        run_id=UUID("00000000-0000-0000-0000-000000000010"),
        task_id=UUID("00000000-0000-0000-0000-000000000020"),
        task_revision=3,
        primitive="crawl",
        requested_url="https://example.com/start",
        normalized_url="https://example.com/start",
        final_url="https://example.com/final",
        captured_at=captured_at or datetime(2026, 7, 10, 12, 1, tzinfo=UTC),
        status_code=200,
        duration_ms=125,
        input_json={"mode": "http", "wait": None},
        input_hash="input:v1",
        crawl_policy_id=UUID("00000000-0000-0000-0000-000000000030"),
        crawl_policy_revision=2,
        warnings_json=[{"code": "redirected"}],
        errors_json=[],
    )


def _elements() -> list[ElementRecord]:
    return [
        ElementRecord(element_index=0, tag="html"),
        ElementRecord(
            element_index=1,
            parent_index=0,
            tag="a",
            attributes={"href": "/first", "rel": "next"},
            text="First",
        ),
        ElementRecord(
            element_index=2,
            parent_index=0,
            tag="div",
            attributes={"href": "/not-a-link"},
        ),
        ElementRecord(
            element_index=3,
            parent_index=0,
            tag="A",
            attributes={"href": ""},
            text="Empty",
        ),
    ]


if __name__ == "__main__":
    unittest.main()
