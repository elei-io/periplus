from __future__ import annotations

import hashlib
import multiprocessing
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from ducklake_client import DiskStorage, DuckLakeAttachConfig, PostgresCatalog
from psycopg import sql
from psycopg.conninfo import make_conninfo

from repository.catalogue import Catalogue, CatalogueConfig, CrawlRecord, UrlRecord
from repository.catalogue.operations import run_with_catalogue_retry
from repository.catalogue.service import CatalogueService
from repository import FileObjectStore, RawHtmlRepository, RepositoryIngestor
from tests.catalogue_helpers import crawl_url_evidence, seed_system_macros


def _concurrent_ingest(
    dsn: str,
    data_path: str,
    object_path: str,
    value: int,
    barrier: multiprocessing.synchronize.Barrier,
    errors: multiprocessing.queues.Queue,
) -> None:
    try:
        html = f'<html><body><a href="/{value}">Page {value}</a></body></html>'
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        catalogue = Catalogue(
            CatalogueConfig(
                catalog=PostgresCatalog(dsn),
                storage=DiskStorage(data_path),
            )
        )
        ingestor = RepositoryIngestor(
            html_repository=RawHtmlRepository(FileObjectStore(Path(object_path))),
            catalogue=catalogue,
            staging_root=Path(object_path).parent / "staging",
        )
        with ingestor:
            barrier.wait(timeout=15)
            ingestor.store_raw(html)
            captured_at = datetime(2026, 7, 11, 12, value, tzinfo=UTC)
            requested_url, urls, attempts = crawl_url_evidence(
                UUID(int=value),
                f"https://example.com/{value}",
                captured_at=captured_at,
                final_url=f"https://example.com/{value}",
            )
            urls = (*urls, UrlRecord.from_normalized_url("https://example.com/shared"))
            prepared = ingestor.prepare_from_raw(
                crawl=CrawlRecord(
                    crawl_id=UUID(int=value),
                    document_id=f"sha256:{digest}",
                    graph_id=UUID(int=100 + value),
                    graph_run_id=UUID(int=200 + value),
                    graph_node_id=UUID(int=300 + value),
                    crawl_request_id=UUID(int=400 + value),
                    requested_url_id=requested_url.url_id,
                    final_url_id=requested_url.url_id,
                    captured_at=captured_at,
                    status_code=200,
                    duration_ms=100,
                    policy_config_json={"value": value},
                    policy_config_hash=f"{value:064x}",
                    outcome="success",
                ),
                urls=urls,
                crawl_attempts=attempts,
            )
            ingestor.commit_prepared_batch([prepared])
    except BaseException as exc:
        errors.put(f"{type(exc).__name__}: {exc}")
        raise


def _concurrent_compact(
    dsn: str,
    data_path: str,
    barrier: multiprocessing.synchronize.Barrier,
    errors: multiprocessing.queues.Queue,
) -> None:
    try:
        config = CatalogueConfig(
            catalog=PostgresCatalog(dsn),
            storage=DiskStorage(data_path),
            attach=DuckLakeAttachConfig(data_inlining_row_limit=0),
        )

        def attempt() -> None:
            with Catalogue(config) as catalogue:
                CatalogueService(catalogue).compact_small_files(
                    minimum_files=4,
                    maximum_input_file_bytes=1024 * 1024,
                    target_file_bytes=2 * 1024 * 1024,
                    maximum_compacted_files=2,
                    maximum_tables=1,
                )

        barrier.wait(timeout=15)
        run_with_catalogue_retry(attempt, description="concurrent test compaction")
    except BaseException as exc:
        errors.put(f"{type(exc).__name__}: {exc}")
        raise


def _concurrent_append(
    dsn: str,
    data_path: str,
    barrier: multiprocessing.synchronize.Barrier,
    errors: multiprocessing.queues.Queue,
) -> None:
    try:
        config = CatalogueConfig(
            catalog=PostgresCatalog(dsn),
            storage=DiskStorage(data_path),
            attach=DuckLakeAttachConfig(data_inlining_row_limit=0),
        )
        with Catalogue(config) as catalogue:
            barrier.wait(timeout=15)
            catalogue.connection.execute(
                "INSERT INTO atlas.main.concurrent_compaction VALUES (8)"
            )
    except BaseException as exc:
        errors.put(f"{type(exc).__name__}: {exc}")
        raise


@unittest.skipUnless(
    os.getenv("ATLAS_TEST_DATABASE_URL"),
    "Postgres catalogue integration is opt-in",
)
class PostgresCatalogueConcurrencyTests(unittest.TestCase):
    def test_append_and_compaction_can_overlap(self) -> None:
        admin_dsn = os.environ["ATLAS_TEST_DATABASE_URL"]
        database_name = f"atlas_catalogue_test_{uuid4().hex}"
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )

        catalogue_dsn = make_conninfo(admin_dsn, dbname=database_name)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = CatalogueConfig(
                    catalog=PostgresCatalog(catalogue_dsn),
                    storage=DiskStorage(root / "lake"),
                    attach=DuckLakeAttachConfig(data_inlining_row_limit=0),
                )
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    catalogue.connection.execute(
                        "CREATE TABLE atlas.main.concurrent_compaction(value INTEGER)"
                    )
                    for value in range(8):
                        catalogue.connection.execute(
                            "INSERT INTO atlas.main.concurrent_compaction VALUES (?)",
                            [value],
                        )

                context = multiprocessing.get_context("spawn")
                barrier = context.Barrier(2)
                errors = context.Queue()
                processes = [
                    context.Process(
                        target=target,
                        args=(catalogue_dsn, str(root / "lake"), barrier, errors),
                    )
                    for target in (_concurrent_compact, _concurrent_append)
                ]
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=30)
                    if process.is_alive():
                        process.kill()
                        process.join()

                failures: list[str] = []
                while not errors.empty():
                    failures.append(errors.get())
                self.assertEqual(
                    [process.exitcode for process in processes],
                    [0, 0],
                    failures,
                )

                with Catalogue(config) as catalogue:
                    self.assertEqual(
                        catalogue.connection.execute(
                            "SELECT count(*), count(DISTINCT value) "
                            "FROM atlas.main.concurrent_compaction"
                        ).fetchone(),
                        (9, 9),
                    )
                    active_files = catalogue.connection.execute(
                        """
                        SELECT count(*)
                        FROM __ducklake_metadata_atlas.ducklake_data_file AS data_file
                        JOIN __ducklake_metadata_atlas.ducklake_table AS table_info
                          ON table_info.table_id = data_file.table_id
                        WHERE table_info.table_name = 'concurrent_compaction'
                          AND table_info.end_snapshot IS NULL
                          AND data_file.end_snapshot IS NULL
                        """
                    ).fetchone()[0]
                    self.assertLessEqual(active_files, 2)
        finally:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )

    def test_hundred_unique_items_use_a_bounded_fence_session_count(self) -> None:
        admin_dsn = os.environ["ATLAS_TEST_DATABASE_URL"]
        database_name = f"atlas_catalogue_test_{uuid4().hex}"
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )

        catalogue_dsn = make_conninfo(admin_dsn, dbname=database_name)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                catalogue = Catalogue(
                    CatalogueConfig(
                        catalog=PostgresCatalog(catalogue_dsn),
                        storage=DiskStorage(root / "lake"),
                    )
                )
                catalogue.bootstrap()
                seed_system_macros(catalogue)
                ingestor = RepositoryIngestor(
                    html_repository=RawHtmlRepository(
                        FileObjectStore(root / "objects")
                    ),
                    catalogue=catalogue,
                    staging_root=root / "staging",
                )
                with ingestor:
                    prepared = []
                    captured_at = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
                    for value in range(1, 101):
                        html = f"<html><body>Unique page {value}</body></html>"
                        identity = ingestor.store_raw(html)
                        observed_at = captured_at + timedelta(seconds=value)
                        requested_url, urls, attempts = crawl_url_evidence(
                            UUID(int=value),
                            f"https://example.com/{value}",
                            captured_at=observed_at,
                            final_url=f"https://example.com/{value}",
                        )
                        prepared.append(
                            ingestor.prepare_from_raw(
                                crawl=CrawlRecord(
                                    crawl_id=UUID(int=value),
                                    document_id=f"sha256:{identity.sha256}",
                                    graph_id=UUID(int=1_000),
                                    graph_run_id=UUID(int=2_000),
                                    graph_node_id=UUID(int=3_000),
                                    crawl_request_id=UUID(int=4_000 + value),
                                    requested_url_id=requested_url.url_id,
                                    final_url_id=requested_url.url_id,
                                    captured_at=observed_at,
                                    status_code=200,
                                    duration_ms=100,
                                    policy_config_json={"value": value},
                                    policy_config_hash=f"{value:064x}",
                                    outcome="success",
                                ),
                                urls=urls,
                                crawl_attempts=attempts,
                            )
                        )

                    results = ingestor.commit_prepared_batch(prepared)
                    self.assertEqual(len(results), 100)
                    self.assertEqual(
                        catalogue.lake.sql_scalar(
                            "SELECT count(*) FROM atlas.main.documents"
                        ),
                        100,
                    )
                    self.assertEqual(
                        catalogue.lake.sql_scalar(
                            "SELECT count(*) FROM atlas.main.crawls"
                        ),
                        100,
                    )
        finally:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )

    def _run_concurrent_ingests(
        self,
        values: tuple[int, int],
    ) -> tuple[int, int, int, bool]:
        admin_dsn = os.environ["ATLAS_TEST_DATABASE_URL"]
        database_name = f"atlas_catalogue_test_{uuid4().hex}"
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
            )

        catalogue_dsn = make_conninfo(admin_dsn, dbname=database_name)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                config = CatalogueConfig(
                    catalog=PostgresCatalog(catalogue_dsn),
                    storage=DiskStorage(root / "lake"),
                )
                with Catalogue(config) as catalogue:
                    catalogue.bootstrap()
                    seed_system_macros(catalogue)

                context = multiprocessing.get_context("spawn")
                barrier = context.Barrier(2)
                errors = context.Queue()
                processes = [
                    context.Process(
                        target=_concurrent_ingest,
                        args=(
                            catalogue_dsn,
                            str(root / "lake"),
                            str(root / "objects"),
                            value,
                            barrier,
                            errors,
                        ),
                    )
                    for value in values
                ]
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=30)
                    if process.is_alive():
                        process.kill()
                        process.join()

                failures: list[str] = []
                while not errors.empty():
                    failures.append(errors.get())
                self.assertEqual(
                    [process.exitcode for process in processes],
                    [0, 0],
                    failures,
                )

                with Catalogue(config) as catalogue:
                    catalogue.validate_schema()
                    counts = (
                        int(
                            catalogue.lake.sql_scalar(
                                "SELECT count(*) FROM atlas.main.documents"
                            )
                        ),
                        int(
                            catalogue.lake.sql_scalar(
                                "SELECT count(*) FROM atlas.main.crawls"
                            )
                        ),
                        int(
                            catalogue.lake.sql_scalar(
                                "SELECT count(*) FROM atlas.main.urls"
                            )
                        ),
                        bool(
                            catalogue.lake.sql_scalar(
                                "SELECT (SELECT count(*) FROM atlas.main.elements) = "
                                "(SELECT coalesce(sum(element_count), 0) "
                                "FROM atlas.main.documents)"
                            )
                        ),
                    )
                    return counts
        finally:
            with psycopg.connect(admin_dsn, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )

    def test_independent_processes_can_commit_to_one_catalogue(self) -> None:
        self.assertEqual(self._run_concurrent_ingests((1, 2)), (2, 2, 3, True))

    def test_same_logical_crawl_is_idempotent_across_processes(self) -> None:
        self.assertEqual(self._run_concurrent_ingests((1, 1)), (1, 1, 2, True))


if __name__ == "__main__":
    unittest.main()
