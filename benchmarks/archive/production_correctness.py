"""Crash/retry/retirement proof for the production batched archive protocol."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from dataset import fingerprint
from io_store import target_store, cleanup
from periplus.ingestion.archive import Archive, ArchiveConflict, CaptureRetired
from periplus.ingestion.objects.store import FileObjectStore
from run import source_store, emit
from test_layouts import fixture


def destination(local_root, root, suffix):
    source = FileObjectStore(Path(local_root)) if local_root else source_store(64)
    return target_store(source, root, suffix)


def write(store, captures, bodies):
    for key, body in bodies.items():
        store.put_if_absent(key, BytesIO(body))
    return Archive(store).commit_many(captures)


def killed(local_root, root, suffix, boundary, captures, bodies):
    store = destination(local_root, root, suffix)
    store.fault = lambda key: os._exit(73) if boundary in key else None
    write(store, captures, bodies)


def recover(store):
    archive = Archive(store)
    return [archive.read_event(e) for e in archive.events(archive.heads())
            if e.kind == "capture" and not archive.retired(e.capture_id)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--s3", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = "benchmarks/archive-layout-" + uuid4().hex
    emit(args.output, dict(phase="production_correctness_start", root=root, s3=args.s3))
    captures, loader = fixture()
    bodies = {c.payload.object_key: loader(c) for c in captures}
    with TemporaryDirectory() as directory:
        local_root = None if args.s3 else directory
        for index, boundary in enumerate(("html/", "/journal/")):
            suffix = f"crash/{index}"
            child = multiprocessing.get_context("spawn").Process(
                target=killed, args=(local_root, root, suffix, boundary, captures, bodies),
            )
            child.start()
            child.join(90)
            if child.is_alive():
                child.kill()
                child.join()
                raise RuntimeError("Crash probe timed out")
            assert child.exitcode == 73
            store = destination(local_root, root, suffix)
            events = write(store, captures, bodies)
            assert events == write(store, captures, bodies)
            assert fingerprint(recover(destination(local_root, root, suffix))) == fingerprint(captures)
            cleanup(store)
            assert store.footprint()["objects"] == 0
            emit(args.output, dict(phase="crash_pass", boundary=boundary, root=root))

        suffix = "overlap"
        store = destination(local_root, root, suffix)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: write(destination(local_root, root, suffix), captures, bodies), range(4)))
        assert all(result == results[0] for result in results)
        assert fingerprint(recover(store)) == fingerprint(captures)
        archive = Archive(store)
        heads = archive.heads()
        try:
            archive.commit(captures[0].model_copy(update={"http_status": 201}))
            raise AssertionError("Conflicting evidence acknowledged")
        except ArchiveConflict:
            pass
        assert archive.heads() == heads
        # A published tombstone remains effective when its journal notification
        # is lost, including replay from a manifest cut preceding retirement.
        with patch.object(archive, "_append", side_effect=ConnectionError("lost notification")):
            try:
                archive.retire(captures[0].capture_id)
                raise AssertionError("Failure not injected")
            except ConnectionError:
                pass
        fresh = Archive(destination(local_root, root, suffix))
        try:
            fresh.commit(captures[0])
            raise AssertionError("Retired capture accepted")
        except CaptureRetired:
            pass
        retained = [fresh.read_event(e) for e in fresh.events(heads) if not fresh.retired(e.capture_id)]
        assert fingerprint(retained) == fingerprint(captures[1:])
        retired = fresh.retire(captures[0].capture_id)
        assert fresh.retire(captures[0].capture_id) == retired
        for capture in retained:
            fresh.verify_payload(capture)
        cleanup(store)
        assert store.footprint()["objects"] == 0
        emit(args.output, dict(phase="production_correctness_complete", root=root, remaining_objects=0))


if __name__ == "__main__":
    main()
