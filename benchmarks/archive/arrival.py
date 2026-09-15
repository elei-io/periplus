"""Low-rate durable flushes and whole-batch reclamation, using verified fixtures."""

import argparse
from pathlib import Path
import time
from uuid import uuid4

from dataset import load_inventory, select, describe
from io_store import target_store, cleanup
from layouts import Separate, Packed
from run import source_store, timed, emit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    captures = select(load_inventory(args.inventory), 1000)
    # All fixture bodies already exist; take ordinary sized pages, not extremes.
    captures = [c for c in captures if 10000 < c.payload.byte_length < 500000][:20]
    loader = lambda c: (args.cache / c.payload.content_id).read_bytes()
    source = source_store()
    root = "benchmarks/archive-layout-" + uuid4().hex
    emit(args.output, dict(phase="arrival_start", root=root, input=describe(captures)))
    for kind in ("separate", "cas", "warc"):
        store = target_store(source, root, kind)
        layout = Separate(store) if kind == "separate" else Packed(store, kind)
        latencies = []

        def arrivals():
            for capture in captures:
                start = time.perf_counter()
                # Flush each individual arrival: no hidden minutes of batching.
                layout.ingest([capture], loader)
                latencies.append(time.perf_counter() - start)

        _, ingest = timed(store, arrivals)
        before = store.footprint()
        _, retire = timed(
            store, lambda: layout.retire([str(c.capture_id) for c in captures])
        )
        _, reclaim = timed(store, layout.reclaim)
        assert not layout.recover()
        emit(
            args.output,
            dict(
                phase="arrival_complete",
                kind=kind,
                ingest=ingest,
                per_capture_seconds=latencies,
                footprint=before,
                retire=retire,
                reclaim=reclaim,
                after_reclaim=store.footprint(),
            ),
        )
        cleanup(store)
        assert store.footprint()["objects"] == 0


if __name__ == "__main__":
    main()
