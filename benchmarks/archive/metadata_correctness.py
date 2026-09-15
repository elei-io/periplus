"""Crash/replay and contention proof on local disk or isolated real S3 objects."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dataset import fingerprint
from io_store import target_store, cleanup
from metadata_batch import MetadataBatch, PREFIX, key
from metadata_load import SharedHeadsArchive
from periplus.ingestion.archive import Archive, ArchiveConflict
from periplus.ingestion.objects.store import FileObjectStore
from run import source_store, emit
from test_layouts import fixture


def destination(local_root, root, suffix):
    source = FileObjectStore(Path(local_root)) if local_root else source_store(16)
    return target_store(source, root, suffix)


def write(store, kind, captures, bodies):
    for capture in captures:
        store.put_if_absent(
            capture.payload.object_key, BytesIO(bodies[capture.payload.object_key])
        )
    if kind == "batch":
        MetadataBatch(store).commit(captures, 0)
    else:
        for capture in captures:
            Archive(store).commit(capture)


def killed(local_root, root, suffix, kind, phase, captures, bodies):
    store = destination(local_root, root, suffix)
    store.fault = lambda path: os._exit(73) if phase in path else None
    write(store, kind, captures, bodies)


def recover(store, kind):
    if kind == "batch":
        return MetadataBatch(store).recover()
    archive = Archive(store)
    found = {}
    for event in archive.events(archive.heads()):
        capture = archive.read(event.capture_id, event.digest)
        found[str(capture.capture_id)] = capture
    return list(found.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = "benchmarks/archive-layout-" + uuid4().hex
    emit(args.output, dict(phase="correctness_start", root=root, s3=args.s3))
    captures, loader = fixture()
    bodies = {c.payload.object_key: loader(c) for c in captures}
    with TemporaryDirectory() as directory:
        local_root = None if args.s3 else directory
        for kind, phases in [
            ("current", ["html/", "/captures/", "/journal/", "/committed/"]),
            ("batch", ["html/", PREFIX]),
        ]:
            for number, phase in enumerate(phases):
                suffix = f"crash/{kind}/{number}"
                child = multiprocessing.get_context("spawn").Process(
                    target=killed,
                    args=(local_root, root, suffix, kind, phase, captures, bodies),
                )
                child.start()
                child.join(60)
                if child.is_alive():
                    child.kill()
                    child.join()
                    raise RuntimeError("Crash probe timed out")
                assert child.exitcode == 73
                store = destination(local_root, root, suffix)
                # Retry frozen work with a fresh process cache; ACK only after
                # metadata is read back. No notification or previous head needed.
                write(store, kind, captures, bodies)
                assert fingerprint(recover(store, kind)) == fingerprint(captures)
                for c in recover(store, kind):
                    Archive(store).verify_payload(c)
                cleanup(store)
                assert store.footprint()["objects"] == 0
                emit(
                    args.output,
                    dict(phase="crash_pass", kind=kind, boundary=phase, root=root),
                )

        for kind in ["current", "batch"]:
            store = destination(local_root, root, f"overlap/{kind}")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: write(store, kind, captures, bodies), range(4)))
            assert fingerprint(recover(store, kind)) == fingerprint(captures)
            # Retry with changed batch boundaries, as after losing a buffer.
            write(store, kind, captures[:3], bodies)
            write(store, kind, captures[3:], bodies)
            assert fingerprint(recover(store, kind)) == fingerprint(captures)
            if kind == "batch":
                # Conflicts are detected on replay, not through a per-capture
                # write index. This difference from production is intentional
                # and must be stated in the benchmark conclusions.
                changed = captures[0].model_copy(update={"http_status": 201})
                MetadataBatch(store).commit([changed], 0)
                try:
                    recover(store, kind)
                    raise AssertionError("Conflicting evidence accepted by replay")
                except ArchiveConflict:
                    pass
            cleanup(store)
            assert store.footprint()["objects"] == 0
            emit(
                args.output,
                dict(phase="overlap_repartition_pass", kind=kind, root=root),
            )

        for kind in ["current", "batch"]:
            store = destination(local_root, root, f"lost_reply/{kind}")
            boundary = PREFIX if kind == "batch" else "/journal/"

            def lost_reply(path):
                if boundary in path:
                    store.fault = None
                    raise TimeoutError("Injected reply loss after durable publication")

            store.fault = lost_reply
            try:
                write(store, kind, captures, bodies)
                raise AssertionError("Fault not reached")
            except TimeoutError:
                pass
            write(store, kind, captures, bodies)
            assert fingerprint(recover(store, kind)) == fingerprint(captures)
            cleanup(store)
            assert store.footprint()["objects"] == 0
            emit(args.output, dict(phase="lost_reply_pass", kind=kind, root=root))

        store = destination(local_root, root, "shared_heads")
        for capture in captures:
            store.put_if_absent(
                capture.payload.object_key, BytesIO(bodies[capture.payload.object_key])
            )
        shared = SharedHeadsArchive(store)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(shared.commit, captures * 2))
        assert fingerprint(recover(store, "current")) == fingerprint(captures)
        cleanup(store)
        assert store.footprint()["objects"] == 0
        emit(args.output, dict(phase="shared_heads_pass", root=root))

        store = destination(local_root, root, "corruption")
        write(store, "batch", captures, bodies)
        store.delete(key(0, 1))
        store.put_if_absent(key(0, 1), BytesIO(b"corrupt"))
        try:
            recover(store, "batch")
            raise AssertionError("Corruption accepted")
        except Exception as exc:
            if isinstance(exc, AssertionError):
                raise
        cleanup(store)
        emit(
            args.output,
            dict(
                phase="correctness_complete",
                root=root,
                remaining_objects=store.footprint()["objects"],
            ),
        )


if __name__ == "__main__":
    main()
