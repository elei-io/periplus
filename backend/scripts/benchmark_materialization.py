"""Benchmark the production document pipeline against a disposable shadow."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from uuid import uuid4

from atlas.materialization.cdc.events import ensure_cdc_stream
from atlas.platform.config.performance import MATERIALIZATION_QUACK_CLIENTS
from atlas.materialization.document_projection import DocumentProjectionSource
from atlas.materialization.document_workload import document_stage_plan
from atlas.materialization.lanes import MaterializationLane, MaterializationLanePool
from atlas.materialization.pipeline import StageSelection, execute_bounded_stage
from atlas.platform.catalogue import ServiceAccountTokenProvider
from atlas.platform.catalogue.duckbasin import DuckBasinConfig
from atlas.platform.catalogue.schema import HTML_ELEMENTS
from atlas.ingestion.objects.config import object_store_from_env
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.platform.messaging.catalogue_workers import CatalogueLaneReporter
from atlas.platform.messaging.client import connect_nats
from atlas.platform.messaging.leases import ensure_operation_lease_storage


async def run(*, documents: int, keep: bool) -> None:
    client = await connect_nats()
    tokens = ServiceAccountTokenProvider(DuckBasinConfig.from_env())
    reporters = tuple(
        CatalogueLaneReporter(lane_index=index)
        for index in range(MATERIALIZATION_QUACK_CLIENTS)
    )
    lanes: list[MaterializationLane] = []
    stats_lane: MaterializationLane | None = None
    shadow = f"_atlas_benchmark_html_{uuid4().hex[:12]}"
    created = False
    try:
        jetstream = client.jetstream()
        await ensure_cdc_stream(jetstream)
        leases = await ensure_operation_lease_storage(jetstream)
        for index in range(MATERIALIZATION_QUACK_CLIENTS):
            lanes.append(
                await MaterializationLane.open(index, tokens=tokens)
            )
        pool = MaterializationLanePool(lanes, reporters)
        snapshot = await pool.call(
            lambda catalogue: catalogue.latest_snapshot()
        )
        if snapshot is None:
            raise RuntimeError("catalogue has no committed snapshot")
        await pool.call(
            lambda catalogue: catalogue.create_materialization_generations(
                {HTML_ELEMENTS: shadow},
                generation_id=shadow,
            )
        )
        created = True
        await asyncio.gather(
            *(
                lane.call(lambda catalogue: catalogue.refresh_metadata())
                for lane in lanes
            )
        )

        def select(catalogue):
            rows = catalogue.trusted_remote_rows(
                f"""
                SELECT content_sha256, object_key, storage_encoding,
                       content_bytes
                FROM (
                  SELECT content_sha256, object_key, storage_encoding,
                         content_bytes,
                         row_number() OVER (
                           PARTITION BY content_sha256
                           ORDER BY
                             CASE storage_encoding
                               WHEN 'zstd' THEN 0 ELSE 1
                             END,
                             object_key
                         ) AS candidate_index
                  FROM ingest.documents AT (VERSION => {snapshot})
                  WHERE lower(detected_media_type) = 'text/html'
                )
                WHERE candidate_index = 1
                ORDER BY content_sha256
                LIMIT {documents}
                """
            )
            return StageSelection(
                items=tuple(
                    DocumentProjectionSource(
                        content_sha256=str(content_hash),
                        object_key=str(object_key),
                        storage_encoding=str(storage_encoding),
                        content_bytes=int(content_bytes),
                    )
                    for (
                        content_hash,
                        object_key,
                        storage_encoding,
                        content_bytes,
                    ) in rows
                )
            )

        repository = RawHtmlRepository(
            object_store_from_env(
                maximum_concurrency=MATERIALIZATION_QUACK_CLIENTS
            )
        )
        plan = document_stage_plan(
            repository,
            pool.capacity,
            targets={"html_elements": shadow},
            select=select,
            enabled_targets=frozenset({"html_elements"}),
            item_budget=10_000,
            byte_budget=1024 * 1024 * 1024,
            write_target_bytes=128 * 1024 * 1024,
        )
        result = await execute_bounded_stage(
            leases,
            pool,
            plan,
        )
        await asyncio.gather(*(lane.close() for lane in lanes))
        lanes.clear()
        stats_lane = await MaterializationLane.open(0, tokens=tokens)
        await stats_lane.call(lambda catalogue: catalogue.refresh_metadata())
        target_rows = await stats_lane.call(
            lambda catalogue: catalogue.trusted_remote_rows(
                f"SELECT count(*) FROM material.{shadow}"
            )
        )
        file_stats = await stats_lane.call(
            lambda catalogue: catalogue.trusted_remote_rows(
                f"""
                SELECT count(*), coalesce(sum(data_file_size_bytes), 0),
                       coalesce(min(data_file_size_bytes), 0),
                       coalesce(
                         approx_quantile(data_file_size_bytes, 0.5), 0
                       ),
                       coalesce(
                         approx_quantile(data_file_size_bytes, 0.9), 0
                       ),
                       coalesce(max(data_file_size_bytes), 0)
                FROM ducklake_list_files(
                  current_catalog(), '{shadow}', schema => 'material'
                )
                WHERE data_file IS NOT NULL
                """
            )
        )
        files, bytes_, minimum, p50, p90, maximum = file_stats[0]
        committed_rows = int(target_rows[0][0])
        complete = committed_rows == result.output_rows
        print(
            json.dumps(
                {
                    "source_snapshot": snapshot,
                    "shadow_table": shadow,
                    "source_items": result.source_items,
                    "source_bytes": result.source_bytes,
                    "partitions": result.partitions,
                    "output_rows": result.output_rows,
                    "committed_rows": committed_rows,
                    "row_count_matches": complete,
                    "projection_bytes": result.output_bytes,
                    "select_seconds": result.select_seconds,
                    "project_seconds": result.project_seconds,
                    "upload_seconds": result.write_seconds,
                    "elapsed_seconds": result.elapsed_seconds,
                    "files_produced": int(files),
                    "files": int(files),
                    "file_bytes": int(bytes_),
                    "file_min_bytes": int(minimum),
                    "file_p50_bytes": int(p50),
                    "file_p90_bytes": int(p90),
                    "file_max_bytes": int(maximum),
                },
                sort_keys=True,
            )
        )
        if not complete:
            raise RuntimeError(
                "shadow row count does not match acknowledged output rows: "
                f"{committed_rows} != {result.output_rows}"
            )
    finally:
        if created and not keep:
            try:
                cleanup_lane = stats_lane
                if cleanup_lane is None:
                    if lanes:
                        cleanup_lane = lanes[0]
                    else:
                        cleanup_lane = await MaterializationLane.open(
                            0,
                            tokens=tokens,
                        )
                        stats_lane = cleanup_lane
                await cleanup_lane.call(
                    lambda catalogue: catalogue.drop_materialization_generations(
                        (shadow,)
                    )
                )
            except Exception:
                logging.exception(
                    "failed to remove benchmark shadow %s",
                    shadow,
                )
        await asyncio.gather(
            *(lane.close() for lane in lanes),
            return_exceptions=True,
        )
        if stats_lane is not None:
            await stats_lane.close()
        tokens.close()
        await client.drain()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=10_000)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    if args.documents < 1:
        parser.error("--documents must be positive")
    asyncio.run(run(documents=args.documents, keep=args.keep))


if __name__ == "__main__":
    main()
