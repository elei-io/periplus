"""Single-writer experimental layouts, deliberately outside the production archive.

Packed publication is data -> derived index -> immutable commit. A commit is the
only discovery authority. Indexes are disposable and reconstructible from packs.
This is a benchmark, not a proposed production coordination implementation.
"""

from hashlib import sha256
from datetime import UTC, datetime
from io import BytesIO
import json
import struct
from uuid import UUID, uuid4

import zstandard as zstd
from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter

from periplus.ingestion.archive import Archive
from periplus.ingestion.captures import Capture, canonical
from io_store import read_all


def compress(data: bytes) -> bytes:
    return zstd.ZstdCompressor(level=6).compress(data)


def decompress(data: bytes) -> bytes:
    with zstd.ZstdDecompressor().stream_reader(BytesIO(data)) as stream:
        result = stream.read(256 * 1024 * 1024 + 1)
    if len(result) > 256 * 1024 * 1024:
        raise ValueError("Expanded record exceeds benchmark limit")
    return result


def put(store, key: str, data: bytes) -> None:
    if not store.put_if_absent(key, BytesIO(data)) and read_all(store, key) != data:
        raise ValueError("Conflicting immutable object")


def checked(capture: Capture, body: bytes) -> bytes:
    if (
        len(body) != capture.payload.byte_length
        or sha256(body).hexdigest() != capture.payload.content_id
    ):
        raise ValueError("Body identity mismatch")
    return body


class Separate:
    def __init__(self, store):
        self.store = store
        self.archive = Archive(store)
        self.captures = {}

    def ingest(self, captures, body_loader):
        for capture in captures:
            # Reuse the existing compressed object, as in an archive adoption.
            key = capture.payload.object_key
            if not self.store.exists(key):
                put(self.store, key, body_loader(capture))
            self.archive.commit(capture)
            self.captures[str(capture.capture_id)] = capture

    def recover(self):
        self.captures = {}
        self.archive = Archive(self.store)
        for event in self.archive.events(self.archive.heads()):
            if event.kind == "capture" and not self.archive.retired(event.capture_id):
                capture = self.archive.read(event.capture_id, event.digest)
                self.captures[str(capture.capture_id)] = capture
        return list(self.captures.values())

    def body(self, capture):
        return checked(
            capture, decompress(read_all(self.store, capture.payload.object_key))
        )

    def scan(self):
        for capture in self.captures.values():
            yield capture, self.body(capture)

    def retire(self, identities):
        for identity in identities:
            self.archive.retire(UUID(identity))
            self.captures.pop(identity, None)

    def reclaim(self):
        # Reference-aware benchmark mark/sweep. Immutable evidence metadata stays.
        needed = {c.payload.object_key for c in self.captures.values()}
        for item in self.store.list_objects("html"):
            if item.key not in needed:
                self.store.delete(item.key)


class Packed:
    def __init__(self, store, kind="cas", pack_bytes=64 * 1024 * 1024, batch_size=256):
        self.store, self.kind = store, kind
        self.pack_bytes, self.batch_size = pack_bytes, batch_size
        self.captures, self.bodies, self.commits = {}, {}, []
        self.retired = set()
        self.retirement_evidence = {}

    def _encode(self, records):
        output = BytesIO()
        index = []
        writer = (
            WARCWriter(output, gzip=True, warc_version="WARC/1.1")
            if self.kind == "warc"
            else None
        )
        for header, payload in records:
            start = output.tell()
            if writer:
                capture_facts = header.get("capture", {})
                uri = (
                    header.get("url")
                    or capture_facts.get("effective_url")
                    or capture_facts.get("requested_url")
                    or "urn:sha256:" + header["hash"]
                )
                headers = {
                    "WARC-Record-ID": "<urn:uuid:" + str(uuid4()) + ">",
                    "WARC-Date": "2026-09-15T00:00:00Z",
                }
                if header["kind"] == "body":
                    headers["WARC-Payload-Digest"] = "sha256:" + header["hash"]
                    record = writer.create_warc_record(
                        uri,
                        "resource",
                        payload=BytesIO(payload),
                        warc_content_type="application/octet-stream",
                        warc_headers_dict=headers,
                    )
                elif header["kind"] == "revisit":
                    record = writer.create_revisit_record(
                        uri,
                        "sha256:" + header["hash"],
                        "urn:sha256:" + header["hash"],
                        "2026-09-15T00:00:00Z",
                        warc_headers_dict=headers,
                    )
                else:
                    record = writer.create_warc_record(
                        uri,
                        "metadata",
                        payload=BytesIO(payload),
                        warc_content_type="application/json",
                        warc_headers_dict=headers,
                    )
                # Bench header is preserved independently of the disposable index.
                record.rec_headers.add_header(
                    "Periplus-Benchmark",
                    json.dumps(header, ensure_ascii=True, separators=(",", ":")),
                )
                writer.write_record(record)
                record.raw_stream.close()
            else:
                encoded = canonical(header)
                encoded_body = compress(payload)
                output.write(
                    struct.pack(">4sIQ", b"PCP1", len(encoded), len(encoded_body))
                )
                output.write(encoded)
                output.write(encoded_body)
            index.append({**header, "offset": start, "length": output.tell() - start})
        return output.getvalue(), index

    def _decode(self, data):
        if self.kind == "warc":
            for record in ArchiveIterator(BytesIO(data)):
                header = json.loads(record.rec_headers.get_header("Periplus-Benchmark"))
                yield header, record.raw_stream.read()
        else:
            stream = BytesIO(data)
            while prefix := stream.read(16):
                if len(prefix) != 16:
                    raise ValueError("Truncated pack header")
                magic, header_size, body_size = struct.unpack(">4sIQ", prefix)
                if (
                    magic != b"PCP1"
                    or header_size > 2 * 1024 * 1024
                    or body_size > 256 * 1024 * 1024
                ):
                    raise ValueError("Invalid pack frame")
                header = json.loads(stream.read(header_size))
                encoded = stream.read(body_size)
                if len(encoded) != body_size:
                    raise ValueError("Truncated pack payload")
                yield header, decompress(encoded)

    def _publish(self, records):
        data, index = self._encode(records)
        digest = sha256(data).hexdigest()
        key = "packs/" + digest + (".warc.gz" if self.kind == "warc" else ".pcp")
        put(self.store, key, data)
        index_key = "indexes/" + digest + ".json.zst"
        encoded_index = compress(canonical(index))
        put(self.store, index_key, encoded_index)
        commit = dict(
            pack=key,
            index=index_key,
            sha256=digest,
            index_sha256=sha256(encoded_index).hexdigest(),
        )
        put(self.store, "commits/" + digest + ".json", canonical(commit))
        self.commits.append(commit)
        self._load_index(commit, index)

    def _load_index(self, commit, index):
        for item in index:
            if item["kind"] == "body":
                self.bodies[item["hash"]] = (
                    commit["pack"],
                    item["offset"],
                    item["length"],
                )
            elif item["kind"] == "capture":
                capture = Capture.model_validate(item["capture"])
                identity = str(capture.capture_id)
                previous = self.captures.get(identity)
                if previous and previous.digest != capture.digest:
                    raise ValueError("Conflicting capture identity")
                if identity not in self.retired:
                    self.captures[identity] = capture

    def ingest(self, captures, body_loader):
        records, size, count = [], 0, 0
        seen = set(self.bodies)
        for capture in captures:
            identity = str(capture.capture_id)
            if identity in self.retired:
                raise ValueError("Retired capture")
            if identity in self.captures:
                if self.captures[identity].digest != capture.digest:
                    raise ValueError("Conflicting capture")
                continue
            digest = capture.payload.content_id
            if digest not in seen:
                body = checked(capture, decompress(body_loader(capture)))
                records.append((dict(kind="body", hash=digest), body))
                seen.add(digest)
                size += len(body)
            elif self.kind == "warc":
                records.append(
                    (dict(kind="revisit", hash=digest, url=capture.effective_url), b"")
                )
            envelope = capture.model_dump(mode="json")
            records.append(
                (dict(kind="capture", capture=envelope), canonical(envelope))
            )
            count += 1
            if size >= self.pack_bytes or count >= self.batch_size:
                self._publish(records)
                records, size, count = [], 0, 0
        if records:
            self._publish(records)

    def recover(self, rebuild_indexes=False):
        self.captures, self.bodies, self.commits = {}, {}, []
        self.retired = set()
        self.retirement_evidence = {}
        for item in self.store.list_objects("retired"):
            for record in json.loads(decompress(read_all(self.store, item.key))):
                capture = Capture.model_validate(record["capture"])
                if capture.digest != record["digest"]:
                    raise ValueError("Retirement evidence mismatch")
                identity = str(capture.capture_id)
                self.retired.add(identity)
                self.retirement_evidence[identity] = record
        for item in self.store.list_objects("commits"):
            commit = json.loads(read_all(self.store, item.key))
            if rebuild_indexes or not self.store.exists(commit["index"]):
                data = read_all(self.store, commit["pack"])
                if sha256(data).hexdigest() != commit["sha256"]:
                    raise ValueError("Corrupt pack")
                # Determine physical record boundaries without rewriting WARC IDs.
                index, offset = [], 0
                if self.kind == "warc":
                    iterator = ArchiveIterator(BytesIO(data))
                    for record in iterator:
                        header = json.loads(
                            record.rec_headers.get_header("Periplus-Benchmark")
                        )
                        record.raw_stream.read()
                        index.append(
                            {
                                **header,
                                "offset": iterator.get_record_offset(),
                                "length": iterator.get_record_length(),
                            }
                        )
                else:
                    while offset < len(data):
                        _, hlen, blen = struct.unpack(
                            ">4sIQ", data[offset : offset + 16]
                        )
                        length = 16 + hlen + blen
                        # Full decode verifies each compression frame as well.
                        header, _ = next(self._decode(data[offset : offset + length]))
                        index.append({**header, "offset": offset, "length": length})
                        offset += length
                encoded = compress(canonical(index))
                if sha256(encoded).hexdigest() != commit["index_sha256"]:
                    raise ValueError("Rebuilt index differs")
                put(self.store, commit["index"], encoded)
            else:
                encoded = read_all(self.store, commit["index"])
                if sha256(encoded).hexdigest() != commit["index_sha256"]:
                    raise ValueError("Corrupt index")
                index = json.loads(decompress(encoded))
            self.commits.append(commit)
            self._load_index(commit, index)
        for capture in self.captures.values():
            if capture.payload.content_id not in self.bodies:
                raise ValueError("Unresolved body reference")
        return list(self.captures.values())

    def body(self, capture):
        key, offset, length = self.bodies[capture.payload.content_id]
        header, body = next(self._decode(self.store.range(key, offset, length)))
        if header["hash"] != capture.payload.content_id:
            raise ValueError("Incorrect body locator")
        return checked(capture, body)

    def scan(self):
        # Body-oriented rebuild: parse each unique body once; retain capture joins.
        by_body = {}
        for capture in self.captures.values():
            by_body.setdefault(capture.payload.content_id, []).append(capture)
        emitted = set()
        for commit in self.commits:
            data = read_all(self.store, commit["pack"])
            if sha256(data).hexdigest() != commit["sha256"]:
                raise ValueError("Corrupt pack")
            for header, body in self._decode(data):
                if header["kind"] == "body" and header["hash"] not in emitted:
                    for capture in by_body.get(header["hash"], []):
                        yield capture, checked(capture, body)
                    emitted.add(header["hash"])

    def retire(self, identities):
        records = []
        for identity in sorted(identities):
            record = self.retirement_evidence.get(identity)
            if record is None:
                capture = self.captures[identity]
                record = dict(
                    capture=capture.model_dump(mode="json"),
                    digest=capture.digest,
                    retired_at=datetime.now(UTC).isoformat(),
                )
            records.append(record)
        data = compress(canonical(records))
        put(self.store, "retired/" + sha256(data).hexdigest() + ".json.zst", data)
        self.retired.update(identities)
        self.retirement_evidence.update(
            {r["capture"]["capture_id"]: r for r in records}
        )
        for identity in identities:
            self.captures.pop(identity, None)

    def reclaim(self):
        # Selective pack rewrite, single offline writer. Publication precedes old
        # commit removal; duplicate immutable captures are safe after interruption.
        needed = {c.payload.content_id for c in self.captures.values()}
        for commit in list(self.commits):
            records = list(self._decode(read_all(self.store, commit["pack"])))
            survivors = [
                (h, b)
                for h, b in records
                if (h["kind"] in ("body", "revisit") and h["hash"] in needed)
                or (
                    h["kind"] == "capture"
                    and h["capture"]["capture_id"] not in self.retired
                )
            ]
            if len(survivors) == len(records):
                continue
            if survivors:
                self._publish(survivors)
            self.store.delete("commits/" + commit["sha256"] + ".json")
            self.store.delete(commit["index"])
            self.store.delete(commit["pack"])
        self.recover()

    def orphan_cleanup(self):
        needed = {c[k] for c in self.commits for k in ("pack", "index")}
        for prefix in ("packs", "indexes"):
            for item in self.store.list_objects(prefix):
                if item.key not in needed:
                    self.store.delete(item.key)
