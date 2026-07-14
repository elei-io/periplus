from __future__ import annotations

import hashlib
import multiprocessing
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from ducklake_client import DiskStorage, PostgresCatalog
from psycopg import sql
from psycopg.conninfo import make_conninfo

from repository.catalogue import Catalogue, CatalogueConfig, CrawlRecord
from repository import FileObjectStore, RawHtmlRepository, RepositoryIngestor


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
            prepared = ingestor.prepare_from_raw(
                crawl=CrawlRecord(
                    crawl_id=UUID(int=value),
                    document_id=f"sha256:{digest}",
                    graph_id=UUID(int=100 + value),
                    graph_run_id=UUID(int=200 + value),
                    graph_node_id=UUID(int=300 + value),
                    crawl_request_id=UUID(int=400 + value),
                    requested_url=f"https://example.com/{value}",
                    normalized_url=f"https://example.com/{value}",
                    final_url=f"https://example.com/{value}",
                    captured_at=datetime(2026, 7, 11, 12, value, tzinfo=UTC),
                    status_code=200,
                    duration_ms=100,
                    domain_group="public-web",
                    profile="http",
                    template="http_fast",
                    config_json={"value": value},
                    config_hash=f"{value:064x}",
                    outcome="success",
                )
            )
            ingestor.commit_prepared_batch([prepared])
    except BaseException as exc:
        errors.put(f"{type(exc).__name__}: {exc}")
        raise


@unittest.skipUnless(
    os.getenv("ATLAS_TEST_DATABASE_URL"),
    "Postgres catalogue integration is opt-in",
)
class PostgresCatalogueConcurrencyTests(unittest.TestCase):
    def _run_concurrent_ingests(self, values: tuple[int, int]) -> tuple[int, int, bool]:
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
        self.assertEqual(self._run_concurrent_ingests((1, 2)), (2, 2, True))

    def test_same_logical_crawl_is_idempotent_across_processes(self) -> None:
        self.assertEqual(self._run_concurrent_ingests((1, 1)), (1, 1, True))


if __name__ == "__main__":
    unittest.main()
