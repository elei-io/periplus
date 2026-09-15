"""Compression-only control over a larger, deterministic random capture sample.

This excludes source download/decompression and all storage writes. It prevents
an adoption benchmark (which can reuse existing Zstd objects) being mistaken for
a like-for-like measurement of compression cost on fresh captures.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
from pathlib import Path
import time

from dataset import load_inventory, select, describe
from io_store import read_all
from layouts import checked, decompress, compress
from run import source_store, emit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=10000)
    args = parser.parse_args()
    captures = select(load_inventory(args.inventory), args.size, "random")
    unique = list({c.payload.content_id: c for c in captures}.values())
    source = source_store()

    def fetch(c):
        path = args.cache / c.payload.content_id
        if not path.exists():
            encoded = read_all(source, c.payload.object_key)
            checked(c, decompress(encoded))
            path.write_bytes(encoded)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, unique))
    totals = {
        kind: dict(bytes=0, seconds=0, cpu_seconds=0) for kind in ("zstd6", "gzip9")
    }
    for index, capture in enumerate(unique):
        body = checked(
            capture, decompress((args.cache / capture.payload.content_id).read_bytes())
        )
        codecs = [
            ("zstd6", compress),
            ("gzip9", lambda b: gzip.compress(b, compresslevel=9, mtime=0)),
        ]
        if index % 2:
            codecs.reverse()
        for kind, encode in codecs:
            start, cpu = time.perf_counter(), time.process_time()
            encoded = encode(body)
            totals[kind]["cpu_seconds"] += time.process_time() - cpu
            totals[kind]["seconds"] += time.perf_counter() - start
            totals[kind]["bytes"] += len(encoded)
            decoded = (
                decompress(encoded) if kind == "zstd6" else gzip.decompress(encoded)
            )
            assert decoded == body
    emit(
        args.output,
        dict(phase="codec_complete", input=describe(captures), results=totals),
    )


if __name__ == "__main__":
    main()
