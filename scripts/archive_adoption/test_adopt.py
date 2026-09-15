"""Guard the one-time conversion's evidence and write boundaries."""

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from adopt import MetadataOnlyStore, VerifiedArchive, convert, verify_bytes
from hashlib import sha256
import zstandard
from periplus.ingestion.archive import PREFIX
from periplus.ingestion.objects.store import FileObjectStore


class AdoptionTests(unittest.TestCase):
    def row(self):
        return dict(capture_id=str(uuid4()), requested_url="https://example.com/",
                    effective_url=None, observed_at=None, status_code=None,
                    visit_document_id=None, document_id=None)

    def test_unavailable_is_preserved_without_inventing_time(self):
        row = self.row()
        capture = convert(row)
        self.assertEqual(str(capture.capture_id), row["capture_id"])
        self.assertEqual(capture.completeness, "unavailable")
        self.assertEqual(capture.timestamp_precision, "unknown")

    def test_missing_document_fails(self):
        row = self.row()
        row["visit_document_id"] = str(uuid4())
        with self.assertRaises(ValueError):
            convert(row)

    def test_preserves_body_locator_and_capture_time(self):
        row = self.row()
        row.update(document_id="d", visit_document_id="d", content_sha256="a"*64,
                   content_bytes=10, object_key="raw/html/aa/body.html.zst",
                   stored_bytes=19, storage_encoding="zstd", representation="rendered_html",
                   detected_media_type="text/html", declared_media_type=None, charset="utf-8",
                   observed_at="2026-09-15T00:00:00.123456+00:00")
        capture = convert(row)
        self.assertEqual(capture.payload.object_key, row["object_key"])
        self.assertEqual(capture.captured_at.microsecond, 123456)

    def test_only_archive_metadata_can_be_written(self):
        with TemporaryDirectory() as root:
            store = MetadataOnlyStore(FileObjectStore(Path(root)))
            for key in ("raw/html/body", PREFIX+"../body"):
                with self.assertRaises(ValueError):
                    store.put_if_absent(key, BytesIO(b"no"))
            with self.assertRaises(ValueError):
                store.delete("raw/html/body")
            self.assertTrue(store.put_if_absent(PREFIX+"adoption/test", BytesIO(b"ok")))

    def test_verified_identity_is_required_and_corrupt_bytes_fail(self):
        with TemporaryDirectory() as root:
            raw = FileObjectStore(Path(root))
            data = b"<html>hello</html>"
            compressed = zstandard.ZstdCompressor().compress(data)
            row = self.row()
            row.update(document_id="d", visit_document_id="d", content_sha256=sha256(data).hexdigest(),
                       content_bytes=len(data), object_key="body.zst", stored_bytes=len(compressed),
                       storage_encoding="zstd", representation="rendered_html",
                       detected_media_type="text/html", declared_media_type=None, charset="utf-8")
            capture = convert(row)
            raw.put_if_absent("body.zst", BytesIO(compressed))
            store = MetadataOnlyStore(raw)
            with self.assertRaises(ValueError):
                VerifiedArchive(store, frozenset()).commit(capture)
            identity = verify_bytes(store, capture)
            writer = VerifiedArchive(store, frozenset({identity}))
            first = writer.commit(capture)
            self.assertEqual(first, writer.commit(capture))
            raw.delete("body.zst")
            raw.put_if_absent("body.zst", BytesIO(b"x"*len(compressed)))
            with self.assertRaises((ValueError, zstandard.ZstdError)):
                verify_bytes(store, capture)


if __name__ == "__main__":
    unittest.main()
