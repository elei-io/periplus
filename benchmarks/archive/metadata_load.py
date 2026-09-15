"""Open-loop real new-body archive load. Source objects are read-only fixtures."""

import argparse
from collections import Counter
from collections import defaultdict
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from queue import Queue, Empty
from threading import Lock, Thread, local
import time
from uuid import uuid4
from urllib.parse import quote


from dataset import load_inventory, select, describe, fingerprint
from io_store import MeteredStore, target_store, read_all
from metadata_batch import MetadataBatch
from periplus.ingestion.archive import Archive
from periplus.ingestion.objects.html import RawHtmlRepository
from run import source_store, emit
from layouts import checked, decompress


class WireMeter:
    """Count actual botocore HTTP attempts, including retries and failed requests."""

    def __init__(self, client):
        self.lock = Lock()
        self.calls = Counter()
        client.meta.events.register("before-send.s3.*", self.sent)

    def sent(self, request, event_name, **kwargs):
        with self.lock:
            self.calls[event_name.rsplit(".", 1)[-1]] += 1

    def snapshot(self):
        with self.lock:
            return dict(self.calls)


class TimedStore(MeteredStore):
    def __init__(self, store):
        super().__init__(store)
        self.elapsed = defaultdict(list)

    def record(self, operation, started):
        with self._lock:
            self.elapsed[operation].append(time.perf_counter() - started)

    def put_if_absent(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return super().put_if_absent(*args, **kwargs)
        finally:
            self.record("put", started)

    def exists(self, key):
        started = time.perf_counter()
        try:
            return super().exists(key)
        finally:
            self.record("head", started)

    def size(self, key):
        started = time.perf_counter()
        try:
            return super().size(key)
        finally:
            self.record("head", started)

    @contextmanager
    def open(self, key):
        started = time.perf_counter()
        try:
            with super().open(key) as stream:
                self.record("get", started)
                started = None
                yield stream
        finally:
            if started is not None:
                self.record("get", started)

    def latencies(self):
        return {
            k: dict(
                count=len(v),
                seconds=sum(v),
                p50=percentile(v, 0.5),
                p95=percentile(v, 0.95),
                p99=percentile(v, 0.99),
            )
            for k, v in self.elapsed.items()
        }


class SharedHeadsArchive(Archive):
    """Same format/commit with one shared head cache and per-shard append locks.

    Conditional creation still arbitrates other processes. The locks coordinate
    only this process, not distributed ownership or capture identities.
    """

    def __init__(self, store):
        super().__init__(store)
        self.append_locks = [Lock() for _ in range(16)]

    def _append(self, capture, kind):
        with self.append_locks[capture.capture_id.int % 16]:
            return super()._append(capture, kind)


def upload(store, original, cache, *, encode_header=True):
    raw = decompress((cache / original.payload.content_id).read_bytes())
    checked(original, raw)
    # The complete original URL remains in Capture. S3's redundant object header
    # must be ASCII and bounded; the first exploratory run exposed both limits.
    header_url = quote(original.requested_url, safe=":/?#[]@!$&'()*+,;=%")
    if len(header_url) > 512:
        header_url = "urn:sha256:" + sha256(original.requested_url.encode()).hexdigest()
    if not encode_header:
        header_url = original.requested_url
    stored = RawHtmlRepository(store).put(
        raw.decode("utf-8"),
        source_url=header_url,
        visit_id=original.capture_id,
        observed_at=original.captured_at or datetime.now(UTC),
        content_type="text/html",
    )
    payload = original.payload.model_copy(
        update=dict(
            object_key=stored.object_key, stored_bytes=stored.compressed_size_bytes
        )
    )
    return original.model_copy(update=dict(payload=payload)), stored.created


def percentile(values, p):
    return (
        sorted(values)[min(len(values) - 1, int(len(values) * p))] if values else None
    )


def trial(source, root, kind, captures, args, wire):
    store = TimedStore(target_store(source, root, f"{kind}_{args.rate}").store)
    assert store.footprint()["objects"] == 0
    store.reset()
    before_wire = wire.snapshot()
    lock = Lock()
    tls = local()
    shared_archive = SharedHeadsArchive(store) if kind == "current_shared" else None
    production_archive = Archive(store) if kind == "production" else None
    ready = Queue()
    completed, errors, created_flags = [], [], []
    samples = []
    start = time.perf_counter()
    cpu = time.process_time()
    producer_done = False

    def finish(items):
        now = time.perf_counter()
        with lock:
            completed.extend(
                (i, capture, now, upload_time) for i, capture, upload_time in items
            )

    def publish(items, shard):
        try:
            if production_archive is not None:
                production_archive.commit_many([item[1] for item in items])
            else:
                MetadataBatch(store).commit([item[1] for item in items], shard)
            finish(items)
        except Exception as exc:
            with lock:
                errors.append(
                    dict(stage="metadata", type=type(exc).__name__, count=len(items))
                )

    def body(i, original):
        try:
            capture, created = upload(store, original, args.cache, encode_header=kind != "production")
            with lock:
                created_flags.append(created)
            uploaded = time.perf_counter()
            if kind not in ("batch", "production"):
                if not hasattr(tls, "archive"):
                    tls.archive = shared_archive or Archive(store)
                tls.archive.commit(capture)
                finish([(i, capture, uploaded)])
            else:
                ready.put((i, capture, uploaded))
        except Exception as exc:
            with lock:
                errors.append(
                    dict(
                        stage="body_or_commit",
                        type=type(exc).__name__,
                        count=1,
                        code=getattr(exc, "response", {}).get("Error", {}).get("Code"),
                        capture_id=str(original.capture_id),
                    )
                )

    def batcher():
        batch, first, number = [], None, 0
        with ThreadPoolExecutor(max_workers=16) as publishers:
            while True:
                try:
                    item = ready.get(timeout=0.02)
                    if item is None:
                        if batch:
                            publishers.submit(publish, batch, number % 16)
                        break
                    batch.append(item)
                    first = first or time.perf_counter()
                except Empty:
                    pass
                if batch and (
                    len(batch) >= 64 or time.perf_counter() - first >= args.flush
                ):
                    publishers.submit(publish, batch, number % 16)
                    batch, first, number = [], None, number + 1

    def observe():
        while not producer_done:
            now = time.perf_counter()
            offered = min(len(captures), int((now - start) * args.rate) + 1)
            with lock:
                done = len(completed)
                sample = dict(
                    seconds=now - start,
                    offered=offered,
                    published=done,
                    backlog=offered - done,
                    errors=len(errors),
                )
                samples.append(sample)
            if len(samples) % 10 == 0:
                emit(
                    args.output,
                    dict(phase="progress", kind=kind, rate=args.rate, **sample),
                )
            time.sleep(1)

    observer = Thread(target=observe)
    observer.start()
    batch_thread = Thread(target=batcher) if kind in ("batch", "production") else None
    if batch_thread:
        batch_thread.start()
    # Equal total storage-call concurrency: current uses combined lanes;
    # batching divides the same budget between body and metadata stages.
    body_workers = args.workers if kind not in ("batch", "production") else args.workers - 16
    if body_workers < 1:
        raise ValueError("Batch trial needs at least 17 total worker lanes")
    with ThreadPoolExecutor(max_workers=body_workers) as workers:
        for i, capture in enumerate(captures):
            due = start + i / args.rate
            time.sleep(max(0, due - time.perf_counter()))
            workers.submit(body, i, capture)
        offered_end = time.perf_counter()
        with lock:
            backlog_at_end = len(captures) - len(completed)
    if batch_thread:
        ready.put(None)
        batch_thread.join()
    end = time.perf_counter()
    producer_done = True
    observer.join()
    latency = [done - (start + i / args.rate) for i, c, done, uploaded in completed]
    publication = [done - uploaded for i, c, done, uploaded in completed]
    wire_after = wire.snapshot()
    report = dict(
        phase="load_complete",
        kind=kind,
        rate=args.rate,
        root=root,
        input=describe(captures),
        workers=args.workers,
        body_workers=body_workers,
        flush_seconds=args.flush,
        seconds=end - start,
        offer_seconds=offered_end - start,
        cpu_seconds=time.process_time() - cpu,
        completed=len(completed),
        fresh_uploads=sum(created_flags),
        errors=errors,
        backlog_at_offer_end=backlog_at_end,
        drain_seconds=end - offered_end,
        completed_per_second=len(completed) / (end - start),
        latency_seconds=dict(
            p50=percentile(latency, 0.5),
            p95=percentile(latency, 0.95),
            p99=percentile(latency, 0.99),
            maximum=max(latency, default=0),
        ),
        after_upload_seconds=dict(p95=percentile(publication, 0.95)),
        object_io=store.stats(),
        object_latency=store.latencies(),
        http_attempts={k: v - before_wire.get(k, 0) for k, v in wire_after.items()},
        backlog_samples=samples,
    )
    emit(args.output, report)
    assert not errors and len(completed) == len(captures)
    assert sum(created_flags) == len(captures), "Dedup hit in genuinely new-body trial"
    expected = [c for i, c, done, uploaded in completed]
    recovery_start = time.perf_counter()
    if kind == "batch":
        recovered = MetadataBatch(store).recover()
    else:
        archive = Archive(store)
        recovered = [
            archive.read_event(e)
            for e in archive.events(archive.heads())
        ]
    assert fingerprint(recovered) == fingerprint(expected)
    recovery_seconds = time.perf_counter() - recovery_start
    # Independent final read-back of EVERY retained body, not sampling.
    with ThreadPoolExecutor(max_workers=args.workers) as workers:
        list(workers.map(Archive(store).verify_payload, recovered))
    emit(
        args.output,
        dict(
            phase="verified",
            kind=kind,
            rate=args.rate,
            recovery_seconds=recovery_seconds,
            footprint=store.footprint(),
            fingerprint=fingerprint(recovered),
        ),
    )
    # Only keys enumerated inside this validated UUID destination are deleted.
    keys = tuple(item.key for item in store.list_objects(""))
    store.store.delete_many(keys)
    assert store.footprint()["objects"] == 0
    emit(args.output, dict(phase="cleaned", kind=kind, rate=args.rate, root=root))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rate", type=int, choices=[50, 100], required=True)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--flush", type=float, default=1.0)
    parser.add_argument(
        "--kinds",
        nargs="+",
        choices=["current", "current_shared", "batch", "production"],
        default=["production"],
    )
    args = parser.parse_args()
    if hasattr(Archive, "commit_many") and any(
        kind in {"current", "current_shared"} for kind in args.kinds
    ):
        parser.error("Historical current/current_shared baselines require application source 19d8d9c; use --kinds production for the batched runtime")
    source = source_store(max_pool_connections=max(96, args.workers + 16))
    captures = select(
        load_inventory(args.inventory), args.rate * args.seconds, "unique"
    )
    assert len(captures) == args.rate * args.seconds
    assert len({c.payload.content_id for c in captures}) == len(captures)
    args.cache.mkdir(parents=True, exist_ok=True)
    root = "benchmarks/archive-layout-" + uuid4().hex
    emit(
        args.output,
        dict(phase="start", root=root, input=describe(captures), rate=args.rate),
    )

    def fetch(c):
        path = args.cache / c.payload.content_id
        if not path.exists():
            path.write_bytes(read_all(source, c.payload.object_key))
        checked(c, decompress(path.read_bytes()))

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(fetch, captures))
    emit(args.output, dict(phase="fixtures_ready", root=root))
    wire = WireMeter(source.client)
    for kind in args.kinds:
        trial(source, root, kind, captures, args, wire)


if __name__ == "__main__":
    main()
