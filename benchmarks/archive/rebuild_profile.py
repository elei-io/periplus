"""CPU floor of the real projection, without database/queue/network waits.

This is deliberately not an end-to-end rebuild. Frozen local body fixtures are
verified by the production raw reader. The real projection is unchanged; only
existing-document lookup and write claims are disabled in this process. Native
parse time is nested inside complete DOM time, not an equivalent output path.
"""

import argparse
from collections import defaultdict
from contextlib import contextmanager, nullcontext
import cProfile
from hashlib import sha256
import json
import os
from pathlib import Path
import time

from dataset import describe, load_inventory, select
from run import emit
from periplus.ingestion.archive import Archive

# MaterialStore imports the process-owned engine. Point it at a deliberately
# unreachable diagnostic database before import; this benchmark never connects.
os.environ["PERIPLUS_CONTROL_DATABASE_URL"] = (
    "postgresql+psycopg://unused@127.0.0.1:1/projection_profile_no_database"
)
from periplus.materialization import storage
from periplus.materialization.dom import lexbor


class FixtureStore:
    def __init__(self, cache, captures):
        self.cache = cache
        self.allowed = {c.payload.object_key: c.payload.content_id for c in captures}

    @contextmanager
    def open(self, key):
        with (self.cache / self.allowed[key]).open("rb") as stream:
            yield stream


class ProjectionOnly(storage.MaterialStore):
    def __init__(self):
        pass

    def content(self, identity):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=2000)
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    captures = select(load_inventory(args.inventory), args.size, "unique")
    archive = Archive(FixtureStore(args.cache, captures))
    # No control DB is opened or written by this isolated phase measurement.
    storage.write_claims = lambda identities: nullcontext()
    material = ProjectionOnly()
    stages = defaultdict(lambda: dict(calls=0, wall=0.0, cpu=0.0))

    def wrap(module, name, label):
        original = getattr(module, name)

        def measured(*positional, **keyword):
            wall, cpu = time.perf_counter(), time.process_time()
            try:
                return original(*positional, **keyword)
            finally:
                record = stages[label]
                record["calls"] += 1
                record["wall"] += time.perf_counter() - wall
                record["cpu"] += time.process_time() - cpu

        setattr(module, name, measured)

    for name in ("parse_document", "html_content", "output_row", "links_from_elements"):
        wrap(storage, name, name)
    wrap(lexbor.native, "LexborHTMLParser", "native_parse_nested")
    identities = dict(captures=[], html_documents=[])
    total_nodes = total_elements = wire_bytes = 0
    started, cpu = time.perf_counter(), time.process_time()
    profiler = cProfile.Profile() if args.profile else None
    emit(
        args.output,
        dict(
            phase="projection_start", input=describe(captures), profiled=bool(profiler)
        ),
    )
    if profiler:
        profiler.enable()
    for index, capture in enumerate(captures):
        document, row = material.project(capture, archive, {})
        assert document is not None
        total_elements += len(document["elements"])
        total_nodes += max(
            (e["subtree_end_index"] for e in document["elements"]), default=0
        )
        for table, value, identity in (
            ("html_documents", document, document["document_id"].upper()),
            ("captures", row, str(capture.capture_id)),
        ):
            identities[table].append(
                dict(id=identity, digest=value["output_digest"].upper())
            )
            wall, encode_cpu = time.perf_counter(), time.process_time()
            encoded = json.dumps(
                value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode()
            wire_bytes += len(encoded) + 1
            record = stages["wire_json_once"]
            record["calls"] += 1
            record["wall"] += time.perf_counter() - wall
            record["cpu"] += time.process_time() - encode_cpu
        if (index + 1) % 250 == 0:
            emit(
                args.output,
                dict(
                    phase="projection_progress",
                    captures=index + 1,
                    seconds=time.perf_counter() - started,
                ),
            )
    if profiler:
        profiler.disable()
        profiler.dump_stats(args.profile)
    seconds, cpu_seconds = time.perf_counter() - started, time.process_time() - cpu
    tables = {
        table: dict(
            rows=len(rows),
            fingerprint=sha256(
                json.dumps(sorted(rows, key=lambda r: r["id"]), sort_keys=True).encode()
            ).hexdigest(),
        )
        for table, rows in identities.items()
    }
    emit(
        args.output,
        dict(
            phase="projection_complete",
            profiled=bool(profiler),
            input=describe(captures),
            seconds=seconds,
            cpu_seconds=cpu_seconds,
            captures_per_second=len(captures) / seconds,
            stages=dict(stages),
            tables=tables,
            elements=total_elements,
            nodes=total_nodes,
            compact_wire_bytes=wire_bytes,
            note="DB/claim-free projection plus one compact wire encoding; native parse is nested inside parse_document",
        ),
    )


if __name__ == "__main__":
    main()
