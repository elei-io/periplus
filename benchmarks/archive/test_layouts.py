"""Semantic/failure tests for experimental formats; no homelab access."""

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import multiprocessing
import os
import unittest

from periplus.ingestion.captures import Capture, Payload
from periplus.ingestion.objects.store import FileObjectStore
from io_store import MeteredStore
from layouts import Separate, Packed, compress, put
from io_store import read_all
from warcio.archiveiterator import ArchiveIterator
from dataset import fingerprint


def fixture():
    captures, objects = [], {}
    for i in range(8):
        data = ("<html>Héllo " + str(i // 2) + "</html>").encode()
        digest = sha256(data).hexdigest()
        encoded = compress(data)
        key = f"html/sha256/{digest[:2]}/{digest[2:4]}/{digest}.html.zst"
        objects[key] = encoded
        captures.append(
            Capture(
                capture_id=uuid4(),
                requested_url="https://example.com/" + str(i),
                effective_url="https://example.com/" + str(i),
                captured_at=datetime.now(UTC),
                timestamp_precision="microsecond",
                http_status=200,
                completeness="complete",
                payload=Payload(
                    content_id=digest,
                    byte_length=len(data),
                    object_key=key,
                    stored_bytes=len(encoded),
                    storage_encoding="zstd",
                    representation="rendered_html",
                    media_type="text/html",
                    declared_media_type="text/html",
                    charset="utf-8",
                ),
            )
        )
    return captures, lambda c: objects[c.payload.object_key]


def killed_writer(directory, kind, phase, captures, objects):
    store = MeteredStore(FileObjectStore(Path(directory)))

    def kill(key):
        if phase in key:
            os._exit(73)

    store.fault = kill
    layout = Separate(store) if kind == "separate" else Packed(store, kind)
    layout.ingest(captures, lambda c: objects[c.payload.object_key])


class LayoutTests(unittest.TestCase):
    def test_standard_warc_reader_verifies_record_digests(self):
        with TemporaryDirectory() as directory:
            store = MeteredStore(FileObjectStore(Path(directory)))
            layout = Packed(store, "warc", batch_size=2)
            captures, loader = fixture()
            layout.ingest(captures, loader)
            kinds = []
            for commit in layout.commits:
                for record in ArchiveIterator(
                    BytesIO(read_all(store, commit["pack"])), check_digests=True
                ):
                    record.raw_stream.read()
                    self.assertIsNot(record.digest_checker.passed, False)
                    if record.rec_type == "metadata":
                        self.assertTrue(
                            record.rec_headers.get_header("WARC-Target-URI").startswith(
                                "https://example.com/"
                            )
                        )
                    kinds.append(record.rec_type)
            self.assertEqual(kinds.count("resource"), 4)
            self.assertEqual(kinds.count("metadata"), 8)
            self.assertEqual(kinds.count("revisit"), 4)

    def test_process_death_and_retry(self):
        cases = [
            ("separate", "/journal/"),
            ("separate", "/committed/"),
            ("cas", "packs/"),
            ("warc", "commits/"),
        ]
        for kind, phase in cases:
            with (
                self.subTest(kind=kind, phase=phase),
                TemporaryDirectory() as directory,
            ):
                captures, loader = fixture()
                objects = {c.payload.object_key: loader(c) for c in captures}
                child = multiprocessing.get_context("spawn").Process(
                    target=killed_writer,
                    args=(directory, kind, phase, captures, objects),
                )
                child.start()
                child.join(timeout=20)
                self.assertFalse(child.is_alive())
                self.assertEqual(child.exitcode, 73)
                store = MeteredStore(FileObjectStore(Path(directory)))
                layout = Separate(store) if kind == "separate" else Packed(store, kind)
                layout.recover()
                layout.ingest(captures, loader)
                self.assertEqual(fingerprint(layout.recover()), fingerprint(captures))

    def test_corrupt_pack_is_rejected(self):
        for kind in ("cas", "warc"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                store = MeteredStore(FileObjectStore(Path(directory)))
                layout = Packed(store, kind)
                captures, loader = fixture()
                layout.ingest(captures, loader)
                key = layout.commits[0]["pack"]
                store.delete(key)
                put(store, key, b"corrupt")
                with self.assertRaisesRegex(ValueError, "Corrupt pack"):
                    list(layout.scan())
                with self.assertRaisesRegex(ValueError, "Corrupt pack"):
                    layout.recover(rebuild_indexes=True)

    def test_roundtrip_indexes_retirement_and_reclamation(self):
        for kind in ("separate", "cas", "warc"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                store = MeteredStore(FileObjectStore(Path(directory)))
                layout = (
                    Separate(store)
                    if kind == "separate"
                    else Packed(store, kind, batch_size=2)
                )
                captures, loader = fixture()
                layout.ingest(captures, loader)
                self.assertEqual(fingerprint(layout.recover()), fingerprint(captures))
                self.assertEqual(len(list(layout.scan())), len(captures))
                for c in captures:
                    self.assertEqual(
                        sha256(layout.body(c)).hexdigest(), c.payload.content_id
                    )
                if kind != "separate":
                    for item in list(store.list_objects("indexes")):
                        store.delete(item.key)
                    self.assertEqual(
                        fingerprint(layout.recover()), fingerprint(captures)
                    )
                retired = [str(c.capture_id) for c in captures[:3]]
                layout.retire(retired)
                layout.reclaim()
                self.assertEqual(
                    fingerprint(layout.recover()), fingerprint(captures[3:])
                )
                for capture in captures[:3]:
                    restored = (
                        layout.archive.read(capture.capture_id)
                        if kind == "separate"
                        else Capture.model_validate(
                            layout.retirement_evidence[str(capture.capture_id)][
                                "capture"
                            ]
                        )
                    )
                    self.assertEqual(restored.digest, capture.digest)
                for c in captures[3:]:
                    layout.body(c)

    def test_lost_write_replies_and_orphans(self):
        for kind in ("cas", "warc"):
            for phase in ("packs/", "indexes/", "commits/"):
                with (
                    self.subTest(kind=kind, phase=phase),
                    TemporaryDirectory() as directory,
                ):
                    store = MeteredStore(FileObjectStore(Path(directory)))
                    layout = Packed(store, kind)
                    captures, loader = fixture()

                    def fail(key):
                        if key.startswith(phase):
                            raise ConnectionError(
                                "Injected lost reply after durable write"
                            )

                    store.fault = fail
                    with self.assertRaises(ConnectionError):
                        layout.ingest(captures, loader)
                    store.fault = None
                    layout = Packed(store, kind)
                    recovered = layout.recover()
                    self.assertEqual(
                        len(recovered), len(captures) if phase == "commits/" else 0
                    )
                    layout.ingest(captures, loader)
                    layout.recover()
                    layout.orphan_cleanup()
                    self.assertEqual(
                        fingerprint(layout.recover()), fingerprint(captures)
                    )
                    self.assertEqual(store.footprint()["objects"], 3)


if __name__ == "__main__":
    unittest.main()
