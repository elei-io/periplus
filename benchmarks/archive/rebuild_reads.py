"""Bounded, read-only verified body scans through the real S3 gateway.

First-pass cache state is uncontrolled. A repeated pass reads precisely the
verified first-pass set. No cache is dropped, no archive object is written.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from hashlib import sha256
import json
import multiprocessing
from pathlib import Path
from threading import Lock
import time

from dataset import describe, load_inventory, select
from io_store import MeteredStore
from periplus.ingestion.objects.html import RawHtmlRepository, HtmlIdentity
from run import source_store, emit


def read_group(captures, threads, seconds):
    source = source_store(max_pool_connections=threads + 8)
    store = MeteredStore(source)
    raw = RawHtmlRepository(store)
    iterator = iter(captures)
    lock = Lock()
    completed, errors, latencies = [], [], []
    started, cpu = time.perf_counter(), time.process_time()

    def lane():
        while time.perf_counter() - started < seconds:
            with lock:
                capture = next(iterator, None)
            if capture is None:
                return
            before = time.perf_counter()
            try:
                raw.verify(
                    capture.payload.object_key,
                    expected=HtmlIdentity(
                        sha256=capture.payload.content_id,
                        size_bytes=capture.payload.byte_length,
                    ),
                )
                with lock:
                    completed.append(str(capture.capture_id))
                    latencies.append(time.perf_counter() - before)
            except Exception as exc:
                with lock:
                    errors.append(
                        dict(
                            id=str(capture.capture_id),
                            error=type(exc).__name__,
                            code=getattr(exc, "response", {})
                            .get("Error", {})
                            .get("Code"),
                        )
                    )

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(lambda _: lane(), range(threads)))
    source.client.close()
    return dict(
        ids=completed,
        errors=errors,
        latencies=latencies,
        seconds=time.perf_counter() - started,
        cpu_seconds=time.process_time() - cpu,
        object_io=store.stats(),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=16000)
    parser.add_argument("--exclude-prior", type=int, default=12000)
    parser.add_argument("--processes", type=int, default=4)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--passes", type=int, default=2)
    args = parser.parse_args()
    inventory = load_inventory(args.inventory)
    excluded = {
        c.payload.content_id for c in select(inventory, args.exclude_prior, "unique")
    }
    captures = [
        c
        for c in select(inventory, len(inventory), "unique")
        if c.payload.content_id not in excluded
    ][: args.size]
    assert captures
    for trial in range(args.passes):
        emit(
            args.output,
            dict(
                phase="read_start",
                trial=trial,
                input=describe(captures),
                processes=args.processes,
                threads_per_process=args.threads,
                budget_seconds=args.seconds,
                cache="uncontrolled-first-pass"
                if trial == 0
                else "repeat-of-completed-first-pass",
            ),
        )
        groups = [captures[i :: args.processes] for i in range(args.processes)]
        start = time.perf_counter()
        with ProcessPoolExecutor(
            max_workers=args.processes, mp_context=multiprocessing.get_context("spawn")
        ) as pool:
            results = list(
                pool.map(
                    read_group,
                    groups,
                    [args.threads] * len(groups),
                    [args.seconds] * len(groups),
                )
            )
        seconds = time.perf_counter() - start
        ids = [identity for r in results for identity in r["ids"]]
        assert len(ids) == len(set(ids))
        finished = set(ids)
        passed = [c for c in captures if str(c.capture_id) in finished]
        elapsed = sorted(t for r in results for t in r["latencies"])
        count = len(ids)
        io = dict(
            read_bytes=sum(r["object_io"]["read_bytes"] for r in results),
            gets=sum(r["object_io"]["requests"].get("get", 0) for r in results),
        )
        errors = [e for r in results for e in r["errors"]]
        emit(
            args.output,
            dict(
                phase="read_complete",
                trial=trial,
                requested=len(captures),
                completed=count,
                seconds=seconds,
                verified_per_second=count / seconds,
                input=describe(passed),
                cpu_seconds=sum(r["cpu_seconds"] for r in results),
                object_io=io,
                compressed_mb_per_second=io["read_bytes"] / 1e6 / seconds,
                p50_seconds=elapsed[len(elapsed) // 2] if elapsed else None,
                p95_seconds=elapsed[int(len(elapsed) * 0.95)] if elapsed else None,
                errors=errors,
                identities_digest=sha256(json.dumps(sorted(ids)).encode()).hexdigest(),
            ),
        )
        assert not errors
        captures = passed


if __name__ == "__main__":
    main()
