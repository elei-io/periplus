"""Reproducible archive-layout experiment. Only UUID-scoped targets are writable."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import platform
import random
import resource
import time
from uuid import uuid4

import boto3
from botocore.config import Config

from periplus.ingestion.objects.store import S3ObjectStore, FileObjectStore
from dataset import load_inventory, select, describe, fingerprint
from io_store import target_store, read_all, cleanup
from layouts import Separate, Packed, checked, decompress


def source_store(max_pool_connections=4):
    endpoint = os.environ["ENDPOINT"]
    if not endpoint.startswith("http"):
        endpoint = "http://" + endpoint
    return S3ObjectStore(
        boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=os.environ["REGION"],
            aws_access_key_id=os.environ["ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["SECRET_ACCESS_KEY"],
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                max_pool_connections=max_pool_connections,
            ),
        ),
        bucket=os.environ["BUCKET"],
        prefix="repository",
    )


def timed(store, operation):
    store.reset()
    start, cpu = time.perf_counter(), time.process_time()
    value = operation()
    result = dict(
        seconds=time.perf_counter() - start,
        cpu_seconds=time.process_time() - cpu,
        **store.stats(),
    )
    return value, result


def emit(output, event):
    line = json.dumps(event, sort_keys=True)
    with output.open("a") as stream:
        stream.write(line + "\n")
    print(line, flush=True)


def trial(source, root, kind, captures, loader, suffix, output, workers=1):
    store = target_store(source, root, suffix)
    if store.footprint()["objects"]:
        raise ValueError("Benchmark target already occupied")
    layout = Separate(store) if kind == "separate" else Packed(store, kind)
    report = dict(kind=kind, suffix=suffix, input=describe(captures), workers=workers)

    def ingest():
        if workers == 1:
            layout.ingest(captures, loader)
            return
        # Fixed body-hash ownership keeps all duplicates with one writer. This
        # is not a test of overlapping/reassigned durable delivery ownership.
        groups = [[] for _ in range(workers)]
        for capture in captures:
            groups[int(capture.payload.content_id[:8], 16) % workers].append(capture)

        def write(group):
            writer = Separate(store) if kind == "separate" else Packed(store, kind)
            writer.ingest(group, loader)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(write, groups))

    _, report["ingest"] = timed(store, ingest)
    report["footprint"] = store.footprint()
    emit(output, dict(phase="ingested", **report))
    recovered, report["recover"] = timed(store, layout.recover)
    assert fingerprint(recovered) == fingerprint(captures)

    def scan():
        identities = set()
        for capture, body in layout.scan():
            checked(capture, body)
            identities.add(str(capture.capture_id))
        assert identities == {str(c.capture_id) for c in captures}

    _, report["scan"] = timed(store, scan)
    choices = random.Random(1729).sample(captures, min(100, len(captures)))
    for name, selected in [("random", choices), ("hot", [choices[0]] * 30)]:
        latencies = []

        def reads():
            for c in selected:
                start = time.perf_counter()
                layout.body(c)
                latencies.append(time.perf_counter() - start)

        _, stats = timed(store, reads)
        latencies.sort()
        report[name] = dict(
            **stats,
            p50_seconds=latencies[len(latencies) // 2],
            p95_seconds=latencies[int(len(latencies) * 0.95)],
        )
    if kind != "separate":
        for item in list(store.list_objects("indexes")):
            store.delete(item.key)
        recovered, report["reconstruct_indexes"] = timed(store, layout.recover)
        assert fingerprint(recovered) == fingerprint(captures)
    # Scattered 10% retirement deliberately stresses packed reclamation.
    identities = [str(c.capture_id) for c in captures[::10]]
    _, report["retire"] = timed(store, lambda: layout.retire(identities))
    _, report["reclaim"] = timed(store, layout.reclaim)
    report["after_reclaim"] = store.footprint()
    recovered = layout.recover()
    assert fingerprint(recovered) == fingerprint(
        [c for c in captures if str(c.capture_id) not in identities]
    )
    for c, body in layout.scan():
        checked(c, body)
    report["verified"] = True
    report["process_peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    emit(output, dict(phase="complete", **report))
    _, stats = timed(store, lambda: cleanup(store))
    assert store.footprint()["objects"] == 0
    emit(output, dict(phase="cleaned", suffix=suffix, **stats))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--size", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--workers", type=int, choices=[1, 4], default=1)
    parser.add_argument(
        "--kinds",
        nargs="+",
        choices=["separate", "cas", "warc"],
        default=["separate", "cas", "warc"],
    )
    parser.add_argument(
        "--workload",
        default="representative",
        choices=["representative", "random", "unique", "small", "repeated"],
    )
    parser.add_argument("--local", type=Path)
    args = parser.parse_args()
    root = "benchmarks/archive-layout-" + uuid4().hex
    source = source_store() if not args.local else FileObjectStore(args.local)
    all_captures = load_inventory(args.inventory)
    captures = select(all_captures, args.size, args.workload)
    args.cache.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    emit(
        args.output,
        dict(
            phase="start",
            root=root,
            inventory=describe(all_captures),
            selected=describe(captures),
            platform=platform.platform(),
            workload=args.workload,
            rounds=args.rounds,
        ),
    )

    def fetch(capture):
        path = args.cache / capture.payload.content_id
        if not path.exists():
            encoded = read_all(source, capture.payload.object_key)
            checked(capture, decompress(encoded))
            path.write_bytes(encoded)
        else:
            checked(capture, decompress(path.read_bytes()))

    unique = list({c.payload.content_id: c for c in captures}.values())
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, unique))
    emit(
        args.output,
        dict(
            phase="fixture_verified",
            seconds=time.perf_counter() - start,
            unique_bodies=len(unique),
        ),
    )
    del all_captures
    loader = lambda c: (args.cache / c.payload.content_id).read_bytes()
    for round_number in range(args.rounds):
        kinds = args.kinds
        kinds = kinds[round_number % len(kinds) :] + kinds[: round_number % len(kinds)]
        for kind in kinds:
            trial(
                source,
                root,
                kind,
                captures,
                loader,
                f"{args.workload}/{round_number}/{kind}",
                args.output,
                args.workers,
            )


if __name__ == "__main__":
    main()
