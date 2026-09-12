"""Local full-batch comparison on retained HTML; never connects to production.

Run with uv from packages/periplus, supplying the experiment fixture directory.
Each invocation uses a fresh DuckLake and an isolated local Postgres schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid4, uuid5

import psutil
import pyarrow as pa
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from periplus.crawl.control.content_policies.schemas import ContentPolicySnapshot
from periplus.ingestion.objects.html import RawHtmlRepository, html_object_key
from periplus.ingestion.objects.store import FileObjectStore
from periplus.materialization.batch import commit_prepared_batch, prepare_batch
from periplus.materialization.document_projection import build_visit_batch_context
from periplus.materialization.models import (
    MaterializationAppliedBatchRecord,
    MaterializationRunRecord,
    MaterializationStateRecord,
)
from periplus.materialization.registry import PROJECTIONS, REGISTRY_DIGEST
from periplus.materialization.runtime import _verify_active_generation
from periplus.materialization.state import publish_generation
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.records import (
    AttemptRecord,
    DocumentRecord,
    VisitEvidence,
    VisitRecord,
    attempt_id_for,
    document_id_for,
)
from periplus.platform.catalogue.service import CatalogueService
from periplus.platform.postgres import session
from periplus.retention.models import (
    LakeWriteClaimRecord,
    RetentionObjectRecord,
    RetiredEvidenceRecord,
)


@contextmanager
def sequential(repository, sources, **kwargs):
    context = build_visit_batch_context(repository, sources, **kwargs)

    class Baseline:
        def dictionary_inputs(self):
            return {
                spec.name: spec.rows(context)
                for spec in PROJECTIONS
                if spec.dictionary_key is not None
            }

        def rows(self, spec, dictionaries):
            return spec.rows(replace(context, dictionary_ids=dictionaries))

    yield Baseline()


@contextmanager
def local_control(url):
    if make_url(url).host not in ("localhost", "127.0.0.1"):
        raise ValueError("benchmark control database must be local")
    schema = "bench_" + uuid4().hex
    url = make_url(url).set(drivername="postgresql+psycopg")
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        for model in (
            MaterializationStateRecord,
            MaterializationAppliedBatchRecord,
            MaterializationRunRecord,
            LakeWriteClaimRecord,
            RetiredEvidenceRecord,
            RetentionObjectRecord,
        ):
            model.__table__.create(engine)
        with patch.object(
            session, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
        ):
            yield
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
        admin.dispose()


def seed(catalogue, root, fixture, count):
    entries = json.loads((fixture / "manifest.json").read_text())[:count]
    policy = ContentPolicySnapshot(
        id=uuid5(NAMESPACE_URL, "bench-policy"),
        slug="benchmark",
        scheme="*",
        host="*",
        path_prefix="/",
        path_mode="prefix",
    )
    now = datetime(2026, 9, 12, tzinfo=UTC)
    evidence = []
    for entry in entries:
        key = html_object_key(entry["id"])
        path = root / "objects" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fixture / "objects" / entry["file"], path)
        visit_id = uuid5(NAMESPACE_URL, entry["id"])
        document_id = document_id_for(visit_id)
        attempt_id = attempt_id_for(visit_id, 0)
        evidence.append(
            VisitEvidence(
                visit=VisitRecord(
                    visit_id=visit_id,
                    requested_url=f"https://example.com/{entry['id']}/",
                    admitted_at=now,
                    started_at=now,
                    observed_at=now,
                    finished_at=now,
                    outcome="succeeded",
                    document_id=document_id,
                    capture_policy=policy,
                ),
                document=DocumentRecord(
                    document_id=document_id,
                    visit_id=visit_id,
                    attempt_id=attempt_id,
                    observed_at=now,
                    representation="rendered_html",
                    detected_media_type="text/html",
                    content_sha256=entry["id"],
                    content_bytes=entry["bytes"],
                    object_key=key,
                    storage_encoding="zstd",
                    stored_bytes=entry["compressed_bytes"],
                ),
                attempts=(
                    AttemptRecord(
                        attempt_id=attempt_id,
                        visit_id=visit_id,
                        attempt_index=0,
                        started_at=now,
                        finished_at=now,
                        outcome="succeeded",
                    ),
                ),
            )
        )
    CatalogueService(catalogue).record_visits(evidence)
    return tuple(str(item.visit.visit_id) for item in evidence)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["sequential", "parallel"], required=True)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument(
        "--control-url",
        default="postgresql://periplus:periplus@127.0.0.1:55432/periplus_test",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with (
        local_control(args.control_url),
        Catalogue(
            CatalogueConfig(
                "periplus",
                str(args.output / "metadata.duckdb"),
                str(args.output / "lake"),
                "ducklake",
            )
        ) as catalogue,
    ):
        catalogue.bootstrap()
        visit_ids = seed(catalogue, args.output, args.fixture, args.count)
        repository = RawHtmlRepository(FileObjectStore(args.output / "objects"))
        run = SimpleNamespace(
            id=uuid4(),
            batch_size=args.count,
            registry_digest=REGISTRY_DIGEST,
            generation_tables={spec.name: spec.name for spec in PROJECTIONS},
        )
        batch = SimpleNamespace(
            id=uuid4(), snapshot=catalogue.latest_snapshot(), visit_ids=visit_ids
        )
        peak = [0]
        stop = threading.Event()
        parent = psutil.Process()

        def monitor():
            while not stop.wait(0.05):
                rss = 0
                for process in [parent, *parent.children(recursive=True)]:
                    try:
                        rss += process.memory_info().rss
                    except psutil.Error:
                        pass
                peak[0] = max(peak[0], rss)

        thread = threading.Thread(target=monitor)
        thread.start()
        started = time.perf_counter()
        try:
            if args.mode == "sequential":
                with patch(
                    "periplus.materialization.batch.prepare_projections", sequential
                ):
                    prepared = prepare_batch(catalogue, repository, run, batch)
            else:
                prepared = prepare_batch(catalogue, repository, run, batch)
            preparation = time.perf_counter() - started
            committed = commit_prepared_batch(catalogue, run, batch, prepared)
            validation_started = time.perf_counter()
            publish_generation(run, catalogue.latest_snapshot())
            _verify_active_generation(catalogue, run)
            validation = time.perf_counter() - validation_started
            elapsed = time.perf_counter() - started
        finally:
            stop.set()
            thread.join()
        # Canonical Arrow fingerprints include every value, type and null. Outside timing/RSS.
        fingerprints = {}
        counts = {}
        for spec in PROJECTIONS:
            order = ",".join(spec.identity_columns)
            table = (
                catalogue.trusted_connection.execute(
                    f"SELECT * FROM material.{spec.name} ORDER BY {order}"
                )
                .to_arrow_table()
                .combine_chunks()
            )
            path = args.output / f"{spec.name}.arrow"
            with (
                pa.OSFile(str(path), "wb") as sink,
                pa.ipc.new_file(sink, table.schema) as writer,
            ):
                writer.write_table(table, max_chunksize=8192)
            with path.open("rb") as stream:
                fingerprints[spec.name] = hashlib.file_digest(
                    stream, "sha256"
                ).hexdigest()
            counts[spec.name] = table.num_rows
            del table
        result = {
            "mode": args.mode,
            "count": args.count,
            "total_seconds": elapsed,
            "preparation_seconds": preparation,
            "project_seconds": prepared.project_seconds,
            "parquet_seconds": prepared.parquet_seconds,
            "commit_seconds": committed.commit_seconds,
            "validation_seconds": validation,
            "peak_rss_bytes": peak[0],
            "output_bytes": prepared.output_bytes,
            "fingerprints": fingerprints,
            "rows": counts,
            "registry_digest": REGISTRY_DIGEST,
        }
        (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
