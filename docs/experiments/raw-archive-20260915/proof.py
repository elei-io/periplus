"""Isolated raw-archive comparison and recovery experiment, not runtime code.

Uses a saved, hash-verified HTML sample; never reads application tables. Optional
S3 writes are restricted to localhost and a newly randomized diagnostic prefix.
Run using uv --with warcio==1.8.1; do not add a project dependency for this proof.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import boto3
import zstandard
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import dotenv_values
from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter

DATE = "2026-09-15T00:00:00Z"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def record_id(name: str) -> str:
    return "<urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, name)) + ">"


class MissingContent(Exception):
    pass


class Conflict(Exception):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.calls = Counter()
        self.read_bytes = self.write_bytes = 0

    def put(self, key: str, data: bytes):
        self.calls["put"] += 1
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Complete one object before publishing it. Hard-link gives no-overwrite.
        tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
        with tmp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
            self.write_bytes += len(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise Conflict(key)
        finally:
            tmp.unlink()

    def get(
        self, key: str, offset: int | None = None, length: int | None = None
    ) -> bytes:
        self.calls["range_get" if offset is not None else "get"] += 1
        with (self.root / key).open("rb") as f:
            if offset is not None:
                f.seek(offset)
            result = f.read() if length is None else f.read(length)
        self.read_bytes += len(result)
        return result

    def keys(self, prefix: str = "") -> list[str]:
        self.calls["list"] += 1
        return sorted(
            str(p.relative_to(self.root))
            for p in self.root.rglob("*")
            if p.is_file()
            and str(p.relative_to(self.root)).startswith(prefix)
            and ".tmp-" not in p.name
        )

    def delete(self, key: str):
        self.calls["delete"] += 1
        (self.root / key).unlink(missing_ok=True)

    def footprint(self) -> int:
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())


class S3Store:
    def __init__(self, env_file: Path):
        env = dotenv_values(env_file)
        endpoint = env["PERIPLUS_REPOSITORY_S3_ENDPOINT"]
        if urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Proof S3 mutations require localhost")
        self.bucket = env["PERIPLUS_REPOSITORY_S3_BUCKET"]
        self.prefix = "diagnostics/raw-archive-proof/" + uuid.uuid4().hex + "/"
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=env["PERIPLUS_REPOSITORY_S3_KEY_ID"],
            aws_secret_access_key=env["PERIPLUS_REPOSITORY_S3_SECRET_ACCESS_KEY"],
            region_name=env.get("PERIPLUS_REPOSITORY_S3_REGION") or "us-east-1",
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 0},
                s3={"addressing_style": "path"},
            ),
        )
        self.calls = Counter()
        self.read_bytes = self.write_bytes = 0

    def put(self, key: str, data: bytes):
        self.calls["put"] += 1
        try:
            self.client.put_object(
                Bucket=self.bucket, Key=self.prefix + key, Body=data, IfNoneMatch="*"
            )
            self.write_bytes += len(data)
        except ClientError as exc:
            if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 412:
                raise
            if self.get(key) != data:
                raise Conflict(key) from None

    def get(self, key: str, offset: int | None = None, length: int | None = None):
        self.calls["range_get" if offset is not None else "get"] += 1
        args = {"Bucket": self.bucket, "Key": self.prefix + key}
        if offset is not None:
            args["Range"] = f"bytes={offset}-{offset + length - 1}"
        result = self.client.get_object(**args)
        try:
            data = result["Body"].read()
        finally:
            result["Body"].close()
        self.read_bytes += len(data)
        return data

    def keys(self, prefix: str = ""):
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix):
            self.calls["list"] += 1
            keys.extend(
                row["Key"][len(self.prefix) :] for row in page.get("Contents", [])
            )
        return sorted(keys)

    def delete(self, key: str):
        self.calls["delete"] += 1
        self.client.delete_object(Bucket=self.bucket, Key=self.prefix + key)

    def cleanup(self):
        for key in self.keys():
            self.delete(key)
        assert self.keys() == []


def fixture_bodies(sample: Path) -> dict[str, bytes]:
    manifest = json.loads((sample / "manifest.json").read_text())
    bodies = {}
    for item in manifest:
        raw = zstandard.ZstdDecompressor().decompress(
            (sample / "objects" / item["file"]).read_bytes(),
            max_output_size=32 * 1024 * 1024,
        )
        assert len(raw) == item["bytes"] and digest(raw) == item["id"]
        bodies[item["id"]] = raw
    return bodies


def fixtures(bodies: dict[str, bytes], copies: int) -> list[dict]:
    result = []
    for repeat in range(copies):
        for i, (sha, raw) in enumerate(bodies.items()):
            capture_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"capture:{repeat}:{sha}"))
            result.append(
                {
                    "format_version": 1,
                    "capture_id": capture_id,
                    "captured_at": f"2026-09-{10 + repeat:02d}T12:00:00Z",
                    "requested_url": f"https://fixture.invalid/{i}",
                    "effective_url": f"https://fixture.invalid/{i}",
                    "outcome": "captured",
                    "representation": "rendered_dom_utf8",
                    "content_sha256": sha,
                    "content_bytes": len(raw),
                    "source": {"provider": "periplus", "dataset": "proof"},
                    "response_headers": [["x-repeat", "a"], ["x-repeat", "b"]],
                }
            )
    return result


def warc(resources: dict[str, bytes], captures: list[dict]) -> tuple[bytes, dict]:
    out = io.BytesIO()
    writer = WARCWriter(out, gzip=True, warc_version="WARC/1.1")
    locations = {}
    for sha, body in resources.items():
        start = out.tell()
        record = writer.create_warc_record(
            "urn:sha256:" + sha,
            "resource",
            payload=io.BytesIO(body),
            warc_content_type="application/octet-stream",
            warc_headers_dict={
                "WARC-Date": DATE,
                "WARC-Record-ID": record_id("payload:" + sha),
            },
        )
        writer.write_record(record)
        locations[sha] = (start, out.tell() - start)
    for capture in captures:
        headers = {
            "WARC-Date": DATE,
            "WARC-Record-ID": record_id("capture:" + capture["capture_id"]),
        }
        if capture.get("content_sha256"):
            headers["WARC-Refers-To"] = record_id(
                "payload:" + capture["content_sha256"]
            )
        writer.write_record(
            writer.create_warc_record(
                capture["requested_url"],
                "metadata",
                payload=io.BytesIO(encode(capture)),
                warc_content_type="application/json",
                warc_headers_dict=headers,
            )
        )
    return out.getvalue(), locations


def read_warc(data: bytes):
    resources, captures = {}, []
    for record in ArchiveIterator(io.BytesIO(data), check_digests=True):
        body = record.raw_stream.read()
        if record.rec_type == "resource":
            sha = record.rec_headers.get_header("WARC-Target-URI").removeprefix(
                "urn:sha256:"
            )
            if digest(body) != sha:
                raise Conflict("payload digest")
            if record.rec_headers.get_header("WARC-Record-ID") != record_id(
                "payload:" + sha
            ):
                raise Conflict("resource identity")
            resources[sha] = body
        elif record.rec_type == "metadata":
            capture = json.loads(body)
            if capture.get("content_sha256") and record.rec_headers.get_header(
                "WARC-Refers-To"
            ) != record_id("payload:" + capture["content_sha256"]):
                raise Conflict("metadata reference")
            captures.append(capture)
        else:
            raise Conflict("unexpected native record type")
        if record.digest_checker and record.digest_checker.problems:
            raise Conflict("WARC block/payload checksum")
    return resources, captures


def capture_map(captures: list[dict], deleted: set[str]) -> dict[str, dict]:
    result = {}
    for capture in captures:
        key = capture["capture_id"]
        if key in deleted:
            continue
        if key in result and result[key] != capture:
            raise Conflict("capture identity")
        result[key] = capture
    return result


def tombstones(store) -> set[str]:
    return {json.loads(store.get(key))["capture_id"] for key in store.keys("deleted/")}


def install_cas(store, bodies: dict[str, bytes], captures: list[dict]):
    for sha, raw in bodies.items():
        store.put(
            "content/" + sha + ".zst", zstandard.ZstdCompressor(level=6).compress(raw)
        )
    for capture in captures:
        store.put("captures/" + capture["capture_id"] + ".json", encode(capture))


def recover_cas(store):
    deleted = tombstones(store)
    captures = capture_map(
        [json.loads(store.get(key)) for key in store.keys("captures/")], deleted
    )
    bodies = {}
    for capture in captures.values():
        sha = capture.get("content_sha256")
        if sha and sha not in bodies:
            try:
                raw = zstandard.ZstdDecompressor().decompress(
                    store.get("content/" + sha + ".zst"),
                    max_output_size=32 * 1024 * 1024,
                )
            except Exception as exc:
                raise MissingContent(sha) from exc
            if digest(raw) != sha:
                raise Conflict("payload digest")
            bodies[sha] = raw
    return captures, bodies


def active_archives(store):
    # Physical replacement receipts are authoritative; lookup indexes are not.
    receipts = [json.loads(store.get(k)) for k in store.keys("replacements/")]
    retired = {k for receipt in receipts for k in receipt["inputs"]}
    outputs = {k for receipt in receipts for k in receipt["outputs"]}
    required = outputs - retired
    existing = set(store.keys("packs/"))
    if not required <= existing:
        raise MissingContent("committed replacement missing")
    return sorted((set(store.keys("hot/")) | required) - retired)


def recover_packed(store):
    bodies = {}
    rows = []
    for key in active_archives(store):
        rb, rc = read_warc(store.get(key))
        bodies.update(rb)
        rows.extend(rc)
    captures = capture_map(rows, tombstones(store))
    needed = {c["content_sha256"] for c in captures.values() if c.get("content_sha256")}
    if not needed <= bodies.keys():
        raise MissingContent("archive payload reference")
    return captures, {sha: bodies[sha] for sha in needed}


def install_hot(store, bodies, captures):
    for capture in captures:
        sha = capture.get("content_sha256")
        data, _ = warc({sha: bodies[sha]} if sha else {}, [capture])
        store.put("hot/" + capture["capture_id"] + ".warc.gz", data)


def compact(store, *, failpoint: str | None = None, reader_active: bool = False):
    inputs = active_archives(store)
    captures, bodies = recover_packed(store)
    # One compactor; this fixture packs all candidates. Production must bound bytes.
    data, locations = warc(bodies, list(captures.values()))
    key = "packs/" + digest(data) + ".warc.gz"
    if inputs == [key]:
        return key, locations
    store.put(key, data)
    checked_bodies, checked_captures = read_warc(store.get(key))
    assert capture_map(checked_captures, set()) == captures and checked_bodies == bodies
    if failpoint == "after_output":
        os._exit(31)
    receipt = {
        "version": 1,
        "inputs": inputs,
        "outputs": [key],
        "sha256": digest(data),
        "captures": len(captures),
    }
    store.put("replacements/" + digest(encode(receipt)) + ".json", encode(receipt))
    if failpoint == "after_receipt":
        os._exit(32)
    if reader_active:
        return key, locations
    for i, old in enumerate(inputs):
        store.delete(old)
        if i == 0 and failpoint == "during_delete":
            os._exit(33)
    return key, locations


def grouped_metadata_recovery(store, bodies, captures, group_size=64):
    # Candidate A refinement: compress capture envelopes into bounded journals.
    # Payload objects and their hashes remain unchanged. No global content locator.
    start = time.perf_counter()
    for offset in range(0, len(captures), group_size):
        data = zstandard.ZstdCompressor(level=6).compress(
            b"\n".join(encode(c) for c in captures[offset : offset + group_size])
            + b"\n"
        )
        store.put("journals/" + digest(data) + ".jsonl.zst", data)
    write = snapshot_metrics(store, start)
    store.calls.clear()
    store.read_bytes = store.write_bytes = 0
    start = time.perf_counter()
    rows = []
    for key in store.keys("journals/"):
        raw = zstandard.ZstdDecompressor().decompress(
            store.get(key), max_output_size=32 * 1024 * 1024
        )
        rows.extend(json.loads(line) for line in raw.splitlines())
    actual = capture_map(rows, set())
    found = {}
    for c in actual.values():
        sha = c.get("content_sha256")
        if sha and sha not in found:
            raw = zstandard.ZstdDecompressor().decompress(
                store.get("content/" + sha + ".zst"), max_output_size=32 * 1024 * 1024
            )
            assert digest(raw) == sha
            found[sha] = raw
    assert actual == capture_map(captures, set()) and found == bodies
    return {
        "journal_write": write,
        "recovery": snapshot_metrics(store, start),
        "group_size": group_size,
    }


def retire_replaced(store, *, reader_active: bool):
    if reader_active:
        return
    active = set(active_archives(store))
    for key in store.keys("replacements/"):
        for old in json.loads(store.get(key))["inputs"]:
            if old not in active:
                store.delete(old)


def snapshot_metrics(store, started):
    return {
        "seconds": time.perf_counter() - started,
        "requests": dict(store.calls),
        "read_bytes": store.read_bytes,
        "write_bytes": store.write_bytes,
    }


def io_bench(store, bodies, captures):
    start = time.perf_counter()
    install_cas(store, bodies, captures)
    result = {"cas_ingestion": snapshot_metrics(store, start)}
    store.calls.clear()
    store.read_bytes = store.write_bytes = 0
    start = time.perf_counter()
    actual = recover_cas(store)
    assert actual == (capture_map(captures, set()), bodies)
    result["cas_recovery"] = snapshot_metrics(store, start)
    store.calls.clear()
    store.read_bytes = store.write_bytes = 0
    start = time.perf_counter()
    install_hot(store, bodies, captures)
    result["warc_hot_ingestion"] = snapshot_metrics(store, start)
    store.calls.clear()
    store.read_bytes = store.write_bytes = 0
    start = time.perf_counter()
    key, locations = compact(store)
    result["warc_compaction"] = snapshot_metrics(store, start)
    store.calls.clear()
    store.read_bytes = store.write_bytes = 0
    start = time.perf_counter()
    assert recover_packed(store) == actual
    result["warc_recovery"] = snapshot_metrics(store, start)
    rng = random.Random(728)
    chosen = rng.sample(list(bodies), min(32, len(bodies)))
    latency = {}
    for kind in ["cas", "warc"]:
        times = []
        before = store.read_bytes
        for sha in chosen:
            start = time.perf_counter()
            if kind == "cas":
                raw = zstandard.ZstdDecompressor().decompress(
                    store.get("content/" + sha + ".zst"),
                    max_output_size=32 * 1024 * 1024,
                )
            else:
                offset, length = locations[sha]
                found, _ = read_warc(store.get(key, offset, length))
                raw = found[sha]
            assert digest(raw) == sha
            times.append((time.perf_counter() - start) * 1000)
        latency[kind] = {
            "n": len(times),
            "p50_ms": statistics.median(times),
            "p95_ms": sorted(times)[int(0.95 * (len(times) - 1))],
            "read_bytes": store.read_bytes - before,
        }
    result["random_lookup"] = latency
    store.calls.clear()
    store.read_bytes = store.write_bytes = 0
    result["cas_with_grouped_metadata"] = grouped_metadata_recovery(
        store, bodies, captures
    )
    return result


def correctness(root: Path):
    bodies = {digest(raw): raw for raw in [b"<h1>same</h1>", b"<h1>different</h1>"]}
    rows = fixtures(bodies, 2)
    failure = dict(
        rows[0],
        capture_id="failed-capture",
        outcome="timeout",
        content_sha256=None,
        content_bytes=0,
    )
    rows.append(failure)
    expected = capture_map(rows, set())
    passed = []
    for point, code in [
        ("after_output", 31),
        ("after_receipt", 32),
        ("during_delete", 33),
    ]:
        store = Store(root / point)
        install_hot(store, bodies, rows)
        child = subprocess.run(
            [
                sys.executable,
                __file__,
                "--crash-child",
                str(store.root),
                "--failpoint",
                point,
            ],
            capture_output=True,
        )
        assert child.returncode == code, (point, child.returncode, child.stderr[-1000:])
        assert recover_packed(Store(store.root)) == (expected, bodies)
        if point == "after_output":
            compact(store)
        else:
            retire_replaced(store, reader_active=False)
        assert recover_packed(Store(store.root)) == (expected, bodies)
        passed.append("real_process_death_" + point)
    store = Store(root / "readers")
    install_hot(store, bodies, rows)
    old = active_archives(store)
    compact(store, reader_active=True)
    assert all((store.root / k).exists() for k in old)
    retire_replaced(store, reader_active=True)
    for key in old:
        read_warc(store.get(key))
    retire_replaced(store, reader_active=False)
    assert all(not (store.root / k).exists() for k in old)
    assert recover_packed(Store(store.root)) == (expected, bodies)
    passed.append("modeled_reader_drain_preserves_old_files")
    for candidate, install, recover in [
        ("cas", install_cas, recover_cas),
        ("warc", install_hot, recover_packed),
    ]:
        store = Store(root / candidate)
        install(store, bodies, rows)
        deleted = rows[0]["capture_id"]
        store.put("deleted/" + deleted + ".json", encode({"capture_id": deleted}))
        assert recover(store) == (capture_map(rows, {deleted}), bodies)
        # Replay cannot resurrect a tombstoned capture.
        install(store, bodies, [rows[0]])
        assert deleted not in recover(store)[0]
        sha = rows[0]["content_sha256"]
        all_deleted = {r["capture_id"] for r in rows if r.get("content_sha256") == sha}
        for cid in all_deleted:
            store.put("deleted/" + cid + ".json", encode({"capture_id": cid}))
        if candidate == "cas":
            for cid in all_deleted:
                store.delete("captures/" + cid + ".json")
            store.delete("content/" + sha + ".zst")
        else:
            compact(store)
        assert recover(store) == (
            capture_map(rows, all_deleted),
            {k: v for k, v in bodies.items() if k != sha},
        )
        if candidate == "warc":
            for key in active_archives(store):
                assert sha not in read_warc(store.get(key))[0]
        assert recover(Store(store.root))[0] == capture_map(rows, all_deleted)
        passed.append(candidate + "_shared_content_retention_delete_and_clean_recovery")
    # Negative controls: missing referenced body and conflicting capture must fail closed.
    store = Store(root / "missing")
    install_cas(store, bodies, rows)
    store.delete("content/" + rows[0]["content_sha256"] + ".zst")
    try:
        recover_cas(store)
    except MissingContent:
        passed.append("missing_body_rejected")
    else:
        raise AssertionError("Missing body accepted")
    try:
        store.put(
            "captures/" + rows[1]["capture_id"] + ".json",
            encode(dict(rows[1], outcome="changed")),
        )
    except Conflict:
        passed.append("conflicting_capture_rejected")
    else:
        raise AssertionError("Conflicting capture accepted")
    # Commit before notification: clean recovery has no queue or external index.
    store = Store(root / "queue-loss")
    install_cas(store, bodies, rows)
    assert recover_cas(Store(store.root)) == (expected, bodies)
    passed.append("capture_commit_survives_missing_queue_and_indexes")
    # Uncommitted payload is an orphan, not an accepted observation.
    store = Store(root / "orphan")
    install_cas(store, bodies, [])
    assert recover_cas(Store(store.root)) == ({}, {})
    passed.append("payload_without_capture_is_not_a_capture")
    store = Store(root / "repeat-pack")
    install_hot(store, bodies, rows)
    compact(store)
    before = active_archives(store)
    compact(store)
    assert active_archives(store) == before and recover_packed(store) == (
        expected,
        bodies,
    )
    passed.append("repeated_compaction_does_not_retire_its_own_output")
    # Global cross-file references: one older pack contains bodies, a later pack
    # contains observations only. Recovery must be independent of listing order.
    store = Store(root / "cross-file")
    first, _ = warc(bodies, rows[:2])
    second, _ = warc({}, rows[2:])
    store.put("hot/z-older.warc.gz", first)
    store.put("hot/a-later.warc.gz", second)
    assert recover_packed(store) == (expected, bodies)
    passed.append("cross_archive_references_resolve_after_rebuilding_index")
    store.delete("hot/z-older.warc.gz")
    try:
        recover_packed(store)
    except MissingContent:
        passed.append("deleting_referenced_archive_rejected")
    else:
        raise AssertionError("Dangling archive reference accepted")
    # Corruption must be detected even when object and filename still exist.
    store = Store(root / "corruption")
    install_cas(store, bodies, rows)
    key = "content/" + next(iter(bodies)) + ".zst"
    (store.root / key).write_bytes(zstandard.ZstdCompressor().compress(b"wrong bytes"))
    try:
        recover_cas(store)
    except Conflict:
        passed.append("wrong_bytes_under_valid_key_rejected")
    else:
        raise AssertionError("Corrupt payload accepted")
    return passed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sample", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--s3-env", type=Path)
    p.add_argument("--crash-child", type=Path)
    p.add_argument("--failpoint")
    args = p.parse_args()
    if args.crash_child:
        compact(Store(args.crash_child), failpoint=args.failpoint)
        return
    bodies = fixture_bodies(args.sample)
    result = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "sample_contents": len(bodies),
        "sample_logical_bytes": sum(map(len, bodies.values())),
        "sample_manifest_sha256": digest((args.sample / "manifest.json").read_bytes()),
        "native_profile": "WARC resource payloads plus linked Periplus JSON metadata; not HTTP replay",
        "limits": "Single process, one request at a time; fixtures fit in RAM; not a scale benchmark.",
    }
    zstd = zstandard.ZstdCompressor(level=6)
    result["body_compression"] = {
        "zstd6_bytes": sum(len(zstd.compress(v)) for v in bodies.values()),
        "gzip6_bytes": sum(
            len(gzip.compress(v, compresslevel=6, mtime=0)) for v in bodies.values()
        ),
    }
    with tempfile.TemporaryDirectory(prefix="periplus-raw-proof-") as temporary:
        root = Path(temporary)
        result["correctness"] = correctness(root / "correctness")
        result["local"] = {}
        for copies in [1, 4]:
            rows = fixtures(bodies, copies)
            store = Store(root / str(copies))
            stats = io_bench(store, bodies, rows)
            stats["captures"] = len(rows)
            stats["retained_cas_bytes"] = sum(
                (store.root / k).stat().st_size
                for k in store.keys()
                if k.startswith(("content/", "captures/"))
            )
            stats["retained_warc_bytes"] = sum(
                (store.root / k).stat().st_size
                for k in store.keys()
                if k.startswith(("packs/", "replacements/"))
            )
            result["local"][str(copies)] = stats
        if args.s3_env:
            store = S3Store(args.s3_env)
            try:
                # Smaller bounded subset keeps this side investigation light on local Compose.
                subset = dict(list(bodies.items())[:64])
                result["s3"] = io_bench(store, subset, fixtures(subset, 2))
                store.put("conditional-proof", b"one")
                try:
                    store.put("conditional-proof", b"two")
                except Conflict:
                    result["s3"]["conditional_conflict_rejected"] = True
                else:
                    raise AssertionError("Conditional write not enforced")
            finally:
                store.cleanup()
                result.setdefault("s3", {})["cleanup_verified"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
