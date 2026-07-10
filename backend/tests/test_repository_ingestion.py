from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from ducklake_client import DiskStorage, DuckDBCatalog

from catalogue import (
    Catalogue,
    CatalogueConfig,
    CatalogueConflictError,
    CatalogueService,
    CrawlRecord,
)
from dom import links_from_html
from repository import (
    FileObjectStore,
    RawHtmlRepository,
    RepositoryIngestor,
    RepositoryIntegrityError,
    RepositoryLimits,
)


class RepositoryIngestorTests(unittest.TestCase):
    def test_current_projection_still_verifies_raw_content(self) -> None:
        html = "<html><body>verify me</body></html>"
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_repository = RawHtmlRepository(FileObjectStore(root / "objects"))
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=html_repository,
                catalogue=catalogue,
            )
            ingestor.bootstrap()
            with ingestor:
                ingestor.ingest(
                    captured_html=html,
                    crawl=_crawl(1, document_id=document_id),
                )
                key = html_repository.identify(html).object_key
                html_repository.store.delete(key)
                html_repository.store.put_if_absent(key, io.BytesIO(b"not-zstd"))

                with self.assertRaises(RepositoryIntegrityError):
                    ingestor.prepare_from_raw(
                        crawl=_crawl(2, document_id=document_id),
                    )

    def test_documentless_failure_commits_without_a_raw_object(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_repository = RawHtmlRepository(FileObjectStore(root / "objects"))
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=html_repository,
                catalogue=catalogue,
                staging_root=root / "staging",
            )
            ingestor.bootstrap()
            failed = _crawl(1, document_id="sha256:" + "a" * 64).model_copy(
                update={
                    "document_id": None,
                    "status_code": None,
                    "errors_json": [{"message": "browser crashed"}],
                }
            )
            with ingestor:
                prepared = ingestor.prepare_from_raw(crawl=failed)
                result = ingestor.commit_prepared_batch([prepared])[0]
                restored = ingestor.catalogue_service.get_crawl(failed.crawl_id)

            self.assertIsNone(result.document_id)
            self.assertTrue(result.crawl_created)
            self.assertEqual(restored, failed)
            self.assertEqual(list((root / "objects").rglob("*.zst")), [])
            self.assertFalse((root / "staging").exists())

    def test_failed_microbatch_can_retain_staging_for_poison_isolation(self) -> None:
        first_html = "<html><body>first</body></html>"
        second_html = "<html><body>second</body></html>"
        first_digest = hashlib.sha256(first_html.encode("utf-8")).hexdigest()
        second_digest = hashlib.sha256(second_html.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(FileObjectStore(root / "objects")),
                catalogue=catalogue,
                staging_root=root / "staging",
            )
            ingestor.bootstrap()
            with ingestor:
                ingestor.ingest(
                    captured_html=first_html,
                    crawl=_crawl(1, document_id=f"sha256:{first_digest}"),
                )
                prepared = ingestor.prepare(
                    captured_html=second_html,
                    crawl=_crawl(1, document_id=f"sha256:{second_digest}"),
                )
                assert prepared.elements_path is not None

                with self.assertRaises(CatalogueConflictError):
                    ingestor.commit_prepared_batch(
                        [prepared],
                        cleanup_on_error=False,
                    )

                self.assertTrue(prepared.elements_path.exists())
                ingestor.discard_prepared([prepared])
                self.assertFalse(prepared.elements_path.exists())

    def test_microbatch_deduplicates_same_document_and_projection(self) -> None:
        html = '<html><body><a href="/same">Same</a></body></html>'
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(FileObjectStore(root / "objects")),
                catalogue=catalogue,
                staging_root=root / "staging",
            )
            ingestor.bootstrap()
            with ingestor:
                prepared = [
                    ingestor.prepare(
                        captured_html=html,
                        crawl=_crawl(value, document_id=document_id),
                    )
                    for value in (1, 2)
                ]
                results = ingestor.commit_prepared_batch(prepared)
                document_count = catalogue.lake.sql_scalar(
                    "SELECT count(*) FROM atlas.main.documents"
                )
                crawl_count = catalogue.lake.sql_scalar(
                    "SELECT count(*) FROM atlas.main.crawls"
                )
                element_count = catalogue.lake.sql_scalar(
                    "SELECT count(*) FROM atlas.main.elements"
                )
                declared_count = catalogue.lake.sql_scalar(
                    "SELECT element_count FROM atlas.main.documents"
                )

        self.assertEqual(document_count, 1)
        self.assertEqual(crawl_count, 2)
        self.assertEqual(element_count, declared_count)
        self.assertTrue(results[0].document_created)
        self.assertFalse(results[1].document_created)
        self.assertTrue(results[0].crawl_created)
        self.assertTrue(results[1].crawl_created)

    def test_repository_rejects_html_over_its_explicit_budget_before_storage(self) -> None:
        html = "<html><body>too large</body></html>"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = RawHtmlRepository(FileObjectStore(root / "objects"))
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=repository,
                catalogue=catalogue,
                limits=RepositoryLimits(
                    max_html_bytes=4,
                    max_document_elements=100,
                    max_document_staged_bytes=1024 * 1024,
                ),
            )
            with self.assertRaisesRegex(ValueError, "repository budget"):
                ingestor.store_raw(html)
            self.assertEqual(list((root / "objects").rglob("*.zst")), [])

    def test_ingestion_stores_raw_html_and_deduplicates_document_projection(self) -> None:
        html = '<!doctype html><html><body><a href="/docs">Docs</a></body></html>'
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_repository = RawHtmlRepository(FileObjectStore(root / "objects"))
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=html_repository,
                catalogue=catalogue,
            )
            ingestor.bootstrap()
            with ingestor:
                first = ingestor.ingest(
                    captured_html=html,
                    crawl=_crawl(1, document_id=document_id),
                )
                second = ingestor.ingest(
                    captured_html=html,
                    crawl=_crawl(2, document_id=document_id),
                )
                service = CatalogueService(catalogue)
                document = service.get_document(document_id)
                links = service.get_links(document_id)
                document_count = catalogue.lake.sql_scalar(
                    "SELECT count(*) FROM atlas.main.documents"
                )
                crawl_count = catalogue.lake.sql_scalar(
                    "SELECT count(*) FROM atlas.main.crawls"
                )

                assert document is not None
                restored = html_repository.read(document.html_object_key)

        self.assertTrue(first.document_created)
        self.assertFalse(second.document_created)
        self.assertEqual(document_count, 1)
        self.assertEqual(crawl_count, 2)
        self.assertEqual(restored, html)
        self.assertEqual([(link.href, link.text) for link in links], [("/docs", "Docs")])

    def test_committed_crawl_resolves_directly_by_retry_stable_identity(self) -> None:
        html = '<html><body><a href="/resume">Resume</a></body></html>'
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(FileObjectStore(root / "objects")),
                catalogue=catalogue,
            )
            ingestor.bootstrap()
            with ingestor:
                crawl = _crawl(1, document_id=document_id)
                ingestor.ingest(captured_html=html, crawl=crawl)

                resumed = ingestor.resolve_crawl(crawl.crawl_id)

        assert resumed is not None
        self.assertEqual(resumed.crawl, crawl)
        self.assertEqual(resumed.html, html)
        self.assertEqual(
            resumed.links["internal"][0]["href"],
            "https://example.com/resume",
        )

    def test_cache_resolution_reuses_repairs_stale_recipe_and_rejects_missing_raw(self) -> None:
        html = (
            '<!doctype html><html><body><a href="/docs" title="Read">Docs</a>'
            "</body></html>"
        )
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            html_repository = RawHtmlRepository(FileObjectStore(root / "objects"))
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=html_repository,
                catalogue=catalogue,
            )
            ingestor.bootstrap()
            with ingestor:
                ingestor.ingest(
                    captured_html=html,
                    crawl=_crawl(1, document_id=document_id),
                )

                reused = ingestor.resolve_cached_page(
                    normalized_url="https://example.com",
                    input_hash="input:v1",
                )
                assert reused is not None
                self.assertFalse(reused.projection_rebuilt)
                self.assertEqual(
                    reused.links["internal"][0]["href"],
                    "https://example.com/docs",
                )
                self.assertEqual(reused.links["internal"][0]["title"], "Read")

                with patch.object(
                    ingestor.catalogue_service,
                    "count_elements",
                    side_effect=AssertionError("projection reads must not count elements"),
                ):
                    metadata_only = ingestor.resolve_cached_page(
                        normalized_url="https://example.com",
                        input_hash="input:v1",
                        include_html=False,
                        include_links=False,
                    )
                assert metadata_only is not None
                self.assertIsNone(metadata_only.html)
                self.assertIsNone(metadata_only.links)

                catalogue.connection.execute(
                    "UPDATE atlas.main.documents SET parser_version = 'stale' "
                    "WHERE document_id = ?",
                    [document_id],
                )
                repaired_stale = ingestor.resolve_cached_page(
                    normalized_url="https://example.com",
                    input_hash="input:v1",
                )
                assert repaired_stale is not None
                self.assertTrue(repaired_stale.projection_rebuilt)
                self.assertEqual(repaired_stale.links, reused.links)

                self.assertTrue(
                    html_repository.store.delete(repaired_stale.document.html_object_key)
                )
                missing = ingestor.resolve_cached_page(
                    normalized_url="https://example.com",
                    input_hash="input:v1",
                )

        self.assertIsNone(missing)

    def test_cache_resolution_applies_warning_block_rules(self) -> None:
        html = "<html><body>blocked</body></html>"
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(FileObjectStore(root / "objects")),
                catalogue=catalogue,
            )
            ingestor.bootstrap()
            with ingestor:
                ingestor.ingest(
                    captured_html=html,
                    crawl=_crawl(1, document_id=document_id).model_copy(
                        update={"warnings_json": [{"code": "too_small"}]}
                    ),
                )
                hit = ingestor.resolve_cached_page(
                    normalized_url="https://example.com",
                    input_hash="input:v1",
                    cache_block_rules={"quality_warning_codes": ["too_small"]},
                )

        self.assertIsNone(hit)

    def test_cached_links_equal_fresh_projection_for_nested_text_and_base_url(self) -> None:
        html = (
            '<html><head><base href="https://static.example/assets/"></head><body>'
            '<a href="guide"><span>Nested <b>bold</b></span> tail</a>'
            '<a href="https://outside.example/path" title=" Away ">Outside</a>'
            "</body></html>"
        )
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        document_id = f"sha256:{digest}"
        page_url = "https://example.com/start"
        expected = links_from_html(html, page_url=page_url)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            catalogue = Catalogue(
                CatalogueConfig(
                    catalog=DuckDBCatalog(root / "catalog.ducklake"),
                    storage=DiskStorage(root / "lake"),
                )
            )
            ingestor = RepositoryIngestor(
                html_repository=RawHtmlRepository(FileObjectStore(root / "objects")),
                catalogue=catalogue,
            )
            ingestor.bootstrap()
            with ingestor:
                ingestor.ingest(
                    captured_html=html,
                    crawl=_crawl(1, document_id=document_id).model_copy(
                        update={"final_url": page_url}
                    ),
                )
                hit = ingestor.resolve_cached_page(
                    normalized_url="https://example.com",
                    input_hash="input:v1",
                )
                raw_links = ingestor.catalogue_service.get_links(document_id)

        assert hit is not None
        self.assertEqual(hit.links, expected)
        self.assertEqual(raw_links[0].href, "guide")
        self.assertEqual(raw_links[0].text, "Nested bold tail")
        self.assertEqual(raw_links[1].title, "Away")


def _crawl(value: int, *, document_id: str) -> CrawlRecord:
    captured_at = datetime(2026, 7, 10, 12, 0, tzinfo=UTC) + timedelta(seconds=value)
    return CrawlRecord(
        crawl_id=UUID(int=value),
        document_id=document_id,
        run_id=UUID(int=100),
        task_id=UUID(int=200),
        task_revision=1,
        primitive="crawl",
        requested_url="https://example.com",
        normalized_url="https://example.com",
        final_url="https://example.com",
        captured_at=captured_at,
        status_code=200,
        duration_ms=100,
        input_json={"urls": ["https://example.com"]},
        input_hash="input:v1",
    )


if __name__ == "__main__":
    unittest.main()
