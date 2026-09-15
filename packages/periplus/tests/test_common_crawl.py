import gzip
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from operational_state_fixture import operational_state
from periplus.ingestion.archive import Archive, CaptureRetired
from periplus.ingestion.archive_import import archive_record
from periplus.ingestion.common_crawl import CommonCrawlClient, CommonCrawlRecord, decode_record
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.store import FileObjectStore


def fixture(*, url="https://example.com/", identity="<urn:uuid:be35bfb2-7d24-4161-b1c9-3b9bd48125ad>",
            body=b"<!doctype html><meta charset=utf-8><h1>Hello archive</h1>",
            extra_headers=(), extra_warc=None):
    output = BytesIO()
    writer = WARCWriter(output, gzip=True)
    record = writer.create_warc_record(url, "response", payload=BytesIO(body),
        http_headers=StatusAndHeaders("200 OK", [("Content-Type", "text/html; charset=utf-8"),
            ("X-Duplicate", "one"), ("X-Duplicate", "two"), *extra_headers], protocol="HTTP/1.1"),
        warc_headers_dict={"WARC-Date": "2026-08-07T10:44:56Z", "WARC-Record-ID": identity, **(extra_warc or {})})
    writer.write_record(record)
    record.raw_stream.close()
    data = output.getvalue()
    item = CommonCrawlRecord(dataset="CC-MAIN-2026-34", url=url, timestamp="20260807104456",
        filename="crawl-data/CC-MAIN-2026-34/segments/123/warc/test.warc.gz", offset=0, length=len(data))
    return item, data, body


class CommonCrawlTests(unittest.TestCase):
    def setUp(self):
        self.sessions = operational_state(self)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = FileObjectStore(Path(directory.name))

    def test_archive_is_exact_and_can_reconstruct_evidence_without_databases(self):
        item, data, body = fixture()
        evidence = archive_record(self.store, item, data)
        self.assertEqual(evidence.payload.content_id, sha256(body).hexdigest())
        self.assertEqual(RawHtmlRepository(self.store).read_bytes(evidence.payload.object_key), body)
        self.assertEqual(evidence.captured_at, item.captured_at)
        self.assertNotIn("attempts", evidence.model_dump())
        self.assertNotIn("capture_policy", evidence.model_dump())
        jobs = [Archive(self.store).read(e.capture_id) for e in Archive(self.store).events(Archive(self.store).heads())]
        self.assertEqual(jobs[0], evidence)
        self.assertEqual(jobs[0].digest, evidence.digest)
        self.assertEqual(archive_record(self.store, item, data), evidence)
        self.assertEqual(len(list(Archive(self.store).events(Archive(self.store).heads()))), 1)

    def test_cross_url_content_dedupe_preserves_distinct_captures(self):
        item, data, _ = fixture()
        first = archive_record(self.store, item, data)
        item, data, _ = fixture(url="https://example.org/", identity="<urn:uuid:be35bfb2-7d24-4161-b1c9-3b9bd48125ae>")
        second = archive_record(self.store, item, data)
        self.assertNotEqual(first.capture_id, second.capture_id)
        self.assertEqual(first.payload.object_key, second.payload.object_key)
        self.assertEqual(len(list(self.store.list_objects("html/"))), 1)

    def test_http_headers_preserve_duplicates_and_bytes(self):
        import base64
        item, data, _ = fixture()
        capture = decode_record(item, data)
        headers = base64.b64decode(capture.source.http_headers_base64)
        self.assertIn(b"X-Duplicate: one\r\nX-Duplicate: two\r\n", headers)
        self.assertIn(headers, gzip.decompress(data))

    def test_conflicting_identity_cannot_replace_raw_evidence(self):
        item, data, _ = fixture()
        original = archive_record(self.store, item, data)
        item, data, _ = fixture(body=b"<h1>Changed</h1>")
        with self.assertRaisesRegex(ValueError, "[Cc]onflict|mismatch"):
            archive_record(self.store, item, data)
        self.assertEqual(Archive(self.store).read(original.capture_id), original)


    def test_unsupported_and_corrupt_records_fail_before_publication(self):
        cases = [fixture(extra_headers=(("Content-Encoding", "gzip"),)),
                 fixture(extra_headers=(("Transfer-Encoding", "chunked"),)),
                 fixture(extra_warc={"WARC-Truncated": "length"}), fixture(body=b"\xff")]
        for item, data, _ in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                archive_record(self.store, item, data)
        self.assertEqual(list(self.store.list_objects("raw/")), [])

    def test_index_url_time_and_length_are_checked(self):
        item, data, _ = fixture()
        for changes in ({"url": "https://other.invalid/"}, {"timestamp": "20260808104456"}, {"length": len(data) + 1}):
            with self.assertRaises(ValueError):
                decode_record(item.model_copy(update=changes), data)

    def test_modified_payload_is_rejected_by_warc_digest(self):
        item, data, _ = fixture()
        changed = gzip.compress(gzip.decompress(data).replace(b"Hello archive", b"Other archive"))
        with self.assertRaisesRegex(ValueError, "digest"):
            decode_record(item.model_copy(update={"length": len(changed)}), changed)

    def test_http_range_is_required(self):
        item, data, _ = fixture()
        client = CommonCrawlClient(httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=data))))
        self.addCleanup(client.close)
        with self.assertRaisesRegex(ValueError, "exact byte range"):
            client.fetch(item)





if __name__ == "__main__":
    unittest.main()

    def test_retired_capture_is_not_reimported(self):
        item,data,_=fixture()
        capture=archive_record(self.store,item,data)
        Archive(self.store).retire(capture.capture_id)
        with self.assertRaises(CaptureRetired):archive_record(self.store,item,data)
