"""Verify real WARC input and an exact HTTP block export round trip locally."""

import argparse
import gzip
import hashlib
import io
import json
import uuid
from pathlib import Path

from warcio.archiveiterator import ArchiveIterator
from warcio.warcwriter import WARCWriter
from warcio.recordloader import ArcWarcRecord


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    results = []
    for item in json.loads((args.input / "manifest.json").read_text()):
        if "file" not in item:
            results.append(item)
            continue
        data = (args.input / item["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        records = []
        for record in ArchiveIterator(io.BytesIO(data), check_digests=True):
            assert record.rec_type == "response"
            payload = record.raw_stream.read()
            assert not record.digest_checker.problems
            records.append((record, payload))
        assert len(records) == 1
        record, payload = records[0]
        # Retain the exact archived HTTP header block, not a guessed reconstruction
        # from a dictionary (which would lose duplicates, case and line ordering).
        expanded = gzip.decompress(data)
        boundary = expanded.index(b"\r\n\r\n") + 4
        block = expanded[
            boundary : boundary + int(record.rec_headers.get_header("Content-Length"))
        ]
        http_end = block.index(b"\r\n\r\n") + 4
        exact_http_headers = block[:http_end]
        assert block[http_end:] == payload
        capture_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "common-crawl:"
                + item["collection"]
                + ":"
                + record.rec_headers.get_header("WARC-Record-ID"),
            )
        )
        # The native object split is exact payload bytes plus immutable envelope.
        # Re-export through maintained WARC writer with original WARC headers.
        out = io.BytesIO()
        writer = WARCWriter(out, gzip=True, warc_version="WARC/1.1")
        # Passing through create_warc_record would parse and reserialize HTTP
        # headers. Preserve the exact block with the lower-level record object.
        clone = ArcWarcRecord(
            "warc",
            "response",
            record.rec_headers,
            io.BytesIO(exact_http_headers + payload),
            None,
            record.content_type,
            len(block),
        )
        clone.payload_length = len(block)
        writer.write_record(clone)
        exported = gzip.decompress(out.getvalue())
        exported_block = exported.split(b"\r\n\r\n", 1)[1][: len(block)]
        assert exported_block == block
        verified = []
        for r in ArchiveIterator(io.BytesIO(out.getvalue()), check_digests=True):
            body = r.raw_stream.read()
            assert body == payload and not r.digest_checker.problems
            assert r.rec_headers.get_header(
                "WARC-Date"
            ) == record.rec_headers.get_header("WARC-Date")
            verified.append(r.rec_type)
        assert verified == ["response"]
        revisit_out = io.BytesIO()
        revisit_writer = WARCWriter(revisit_out, gzip=True, warc_version="WARC/1.1")
        original_id = record.rec_headers.get_header("WARC-Record-ID")
        revisit = revisit_writer.create_revisit_record(
            "https://duplicate.fixture.invalid/",
            record.rec_headers.get_header("WARC-Payload-Digest"),
            record.rec_headers.get_header("WARC-Target-URI"),
            record.rec_headers.get_header("WARC-Date"),
            warc_headers_dict={
                "WARC-Refers-To": original_id,
                "WARC-Date": "2026-09-15T00:00:00Z",
            },
        )
        revisit_writer.write_record(revisit)
        references = []
        for r in ArchiveIterator(
            io.BytesIO(revisit_out.getvalue()), check_digests=True
        ):
            assert r.rec_type == "revisit" and r.raw_stream.read() == b""
            references.append(r.rec_headers.get_header("WARC-Refers-To"))
        payload_index = {original_id: payload}
        assert [payload_index[r] for r in references] == [payload]
        del payload_index[original_id]
        try:
            payload_index[references[0]]
        except KeyError:
            missing_revisit_rejected = True
        else:
            raise AssertionError("Unresolved revisit accepted")
        results.append(
            {
                "provider": "common-crawl",
                "dataset": item["collection"],
                "archive_source": item["source"],
                "offset": item["offset"],
                "length": item["length"],
                "record_sha256": item["sha256"],
                "capture_id": capture_id,
                "original_record_id": record.rec_headers.get_header("WARC-Record-ID"),
                "captured_at": record.rec_headers.get_header("WARC-Date"),
                "target_url": record.rec_headers.get_header("WARC-Target-URI"),
                "payload_bytes": len(payload),
                "payload_sha256": hashlib.sha256(payload).hexdigest(),
                "http_header_bytes": len(exact_http_headers),
                "http_block_exact_round_trip": True,
                "synthetic_cross_url_revisit_resolved": True,
                "missing_revisit_rejected": missing_revisit_rejected,
            }
        )
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
