"""One-time old-lake adoption using the current archive, never writes body objects."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import gzip
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import threading
from uuid import UUID
import zstandard

from periplus.ingestion.archive import Archive, PREFIX, SHARDS
from periplus.ingestion.captures import Capture, Payload, canonical
from periplus.ingestion.objects.config import object_store_from_env
from periplus.materialization.recipe import preserve_software, recipe_digest


def payload_identity(capture: Capture) -> tuple:
    p = capture.payload
    return (
        p.object_key,
        p.content_id,
        p.byte_length,
        p.stored_bytes,
        p.storage_encoding,
    )


def verify_bytes(store, capture: Capture) -> tuple:
    """One bounded GET checks actual compressed length as well as decoded bytes."""
    p = capture.payload
    if p.byte_length > 256 * 1024 * 1024 or p.stored_bytes > 256 * 1024 * 1024:
        raise ValueError("Payload exceeds adoption byte budget")
    with store.open(p.object_key) as source:
        data = source.read(p.stored_bytes + 1)
    if len(data) != p.stored_bytes:
        raise ValueError("Stored payload length mismatch")
    decoded = BytesIO(data)
    if p.storage_encoding == "zstd":
        decoded = zstandard.ZstdDecompressor().stream_reader(decoded)
    digest = sha256()
    count = 0
    with decoded as stream:
        while chunk := stream.read(min(1024 * 1024, p.byte_length - count + 1)):
            count += len(chunk)
            if count > p.byte_length:
                raise ValueError("Expanded payload exceeds declared length")
            digest.update(chunk)
    if count != p.byte_length or digest.hexdigest() != p.content_id:
        raise ValueError("Payload digest/length mismatch")
    return payload_identity(capture)


class VerifiedArchive(Archive):
    """Only for an exclusive adoption run with old writers stopped."""

    def __init__(self, store, verified: frozenset[tuple]):
        super().__init__(store)
        self.verified = verified

    def verify_payload(self, capture: Capture) -> None:
        if capture.payload and payload_identity(capture) not in self.verified:
            raise ValueError("Capture body was not verified in this adoption run")


class MetadataOnlyStore:
    """Reuse normal reads; refuse body writes and every deletion."""

    def __init__(self, store):
        self.store = store

    def __getattr__(self, name):
        if name.startswith("delete"):
            raise ValueError("Adoption cannot delete objects")
        return getattr(self.store, name)

    def put_if_absent(self, key, content, *, headers=None):
        if not key.startswith(PREFIX) or ".." in key.split("/"):
            raise ValueError("Adoption may only create archive metadata")
        return self.store.put_if_absent(key, content, headers=headers)


def convert(row: dict) -> Capture:
    if row["visit_document_id"] != row["document_id"]:
        raise ValueError("Missing/mismatched source document")
    payload = None
    if row["document_id"]:
        payload = Payload(
            content_id=row["content_sha256"],
            byte_length=row["content_bytes"],
            object_key=row["object_key"],
            stored_bytes=row["stored_bytes"],
            storage_encoding=row["storage_encoding"],
            representation=row["representation"],
            media_type=row["detected_media_type"],
            declared_media_type=row["declared_media_type"],
            charset=row["charset"],
        )
    return Capture(
        capture_id=UUID(row["capture_id"]),
        requested_url=row["requested_url"],
        effective_url=row["effective_url"],
        captured_at=datetime.fromisoformat(row["observed_at"])
        if row["observed_at"]
        else None,
        timestamp_precision="microsecond" if row["observed_at"] else "unknown",
        http_status=row["status_code"],
        completeness="complete" if payload else "unavailable",
        payload=payload,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--retired", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument(
        "--verified-report",
        type=Path,
        help="Reuse an exact completed read-only verification while old writers remain stopped",
    )
    args = parser.parse_args()
    if args.verified_report and not args.commit:
        parser.error(
            "--verified-report is only for the exclusive metadata commit phase"
        )
    retired_rows = json.loads(args.retired.read_text())
    retired = {r["identity"] for r in retired_rows if r["kind"] == "observation"}
    captures = {}
    excluded = []
    with gzip.open(args.inventory, "rt") as source:
        header = json.loads(next(source))
        rows = 0
        for line in source:
            row = json.loads(line)
            rows += 1
            if row["capture_id"] in retired:
                excluded.append(row["capture_id"])
                continue
            capture = convert(row)
            key = str(capture.capture_id)
            if key in captures:
                raise ValueError("Duplicate capture identity")
            captures[key] = capture
    if rows != header["visits"]:
        raise ValueError("Incomplete inventory")
    expected = {key: value.digest for key, value in captures.items()}
    fingerprint = sha256(canonical(sorted(expected.items()))).hexdigest()
    store = MetadataOnlyStore(object_store_from_env(maximum_concurrency=16))
    archive = Archive(store)
    bodies = {}
    for capture in captures.values():
        if capture.payload:
            key = capture.payload.object_key
            identity_fields = {
                "content_id",
                "byte_length",
                "stored_bytes",
                "storage_encoding",
            }
            if key in bodies and bodies[key].payload.model_dump(
                include=identity_fields
            ) != capture.payload.model_dump(include=identity_fields):
                raise ValueError("Conflicting body metadata")
            bodies[key] = capture
    report = dict(
        source=header,
        captures=len(captures),
        unique_payload_objects=len(bodies),
        unavailable=sum(c.payload is None for c in captures.values()),
        excluded_retired=excluded,
        fingerprint=fingerprint,
        inventory_sha256=sha256(args.inventory.read_bytes()).hexdigest(),
    )
    if hasattr(store, "bucket"):
        report["repository"] = dict(
            kind="s3",
            endpoint=store.client.meta.endpoint_url,
            bucket=store.bucket,
            prefix=store.prefix,
        )
    else:
        report["repository"] = dict(kind="disk", root=str(store.root))
    print(json.dumps({"phase": "inventory", **report}), flush=True)
    verified = set()
    verification_report_sha256 = None
    if args.verified_report:
        proof = args.verified_report.read_bytes()
        if json.loads(proof) != report:
            raise ValueError(
                "Verification report does not exactly match the frozen input"
            )
        # The report is written only after every payload passes. This operator
        # path requires the same immutable repository and continuously stopped
        # old writers, just as the verification/commit phases of one invocation.
        verified = {payload_identity(c) for c in bodies.values()}
        verification_report_sha256 = sha256(proof).hexdigest()
        print(
            json.dumps(
                {
                    "phase": "verified_payloads_reused",
                    "count": len(verified),
                    "report_sha256": verification_report_sha256,
                }
            ),
            flush=True,
        )
    else:
        with ThreadPoolExecutor(max_workers=16) as pool:
            for index, identity in enumerate(
                pool.map(lambda c: verify_bytes(store, c), bodies.values()), 1
            ):
                verified.add(identity)
                if index % 5000 == 0:
                    print(
                        json.dumps({"phase": "payloads_verified", "count": index}),
                        flush=True,
                    )
        print(
            json.dumps({"phase": "all_payloads_verified", "count": len(bodies)}),
            flush=True,
        )
    if not args.commit:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        return
    # Exact retry allowed. Unknown pre-existing captures/events are rejected.
    for event in archive.events(archive.heads()):
        if (
            event.kind != "capture"
            or expected.get(str(event.capture_id)) != event.digest
        ):
            raise ValueError("Destination contains unrelated/retired evidence")
    progress = 0
    lock = threading.Lock()

    def commit_shard(shard: int) -> None:
        nonlocal progress
        writer = VerifiedArchive(store, frozenset(verified))
        selected = [c for c in captures.values() if c.capture_id.int % SHARDS == shard]
        for offset in range(0, len(selected), 64):
            batch = selected[offset : offset + 64]
            writer.commit_many(batch)
            with lock:
                progress += len(batch)
                if progress // 1000 != (progress - len(batch)) // 1000:
                    print(
                        json.dumps({"phase": "committed", "count": progress}),
                        flush=True,
                    )

    with ThreadPoolExecutor(max_workers=SHARDS) as pool:
        list(pool.map(commit_shard, range(SHARDS)))
    recovered = {}
    heads = archive.heads()
    for event in archive.events(heads):
        if event.kind != "capture":
            raise ValueError("Unexpected retirement during adoption")
        capture = archive.read_event(event)
        recovered[str(capture.capture_id)] = capture.digest
    if recovered != expected:
        raise ValueError("Recovered archive differs from frozen source")
    software = preserve_software(store)
    manifest_key, manifest = archive.manifest(recipe_digest(), software)
    if manifest.heads != heads:
        raise ValueError("Archive changed during final verification")
    report.update(
        verification_report_sha256=verification_report_sha256,
        manifest_key=manifest_key,
        manifest=manifest.model_dump(mode="json"),
        verified_captures=len(recovered),
        journal_events=sum(heads),
    )
    audit = canonical(report)
    audit_key = f"{PREFIX}adoption/{sha256(audit).hexdigest()}.json"
    store.put_if_absent(audit_key, BytesIO(audit))
    report["audit_key"] = audit_key
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"phase": "complete", **report}), flush=True)


if __name__ == "__main__":
    main()
