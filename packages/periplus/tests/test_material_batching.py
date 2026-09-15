"""Batched reads retain retirement fences, admission and exact encoded output."""
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from periplus.ingestion.captures import Capture, Payload, canonical
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.store import FileObjectStore
from periplus.materialization.storage import MaterialStore, output_row, row_bytes


class MaterialBatchingTests(unittest.TestCase):
    def test_encoded_output_has_exact_digest_utf8_and_cannot_replace_fields(self):
        value = {'document_id': 'a' * 64, 'document_text': '猫😀\\"\n'}
        row = output_row(value)
        expected = {**value, 'output_digest': sha256(canonical(value)).hexdigest()}
        self.assertEqual(json.loads(row.wire), expected)
        self.assertEqual(dict(row), expected)
        self.assertEqual(row_bytes(row), len(canonical(expected)))
        with self.assertRaises(TypeError):
            row['document_text'] = 'changed'

    def test_bulk_completion_requires_document_and_rejects_conflicts(self):
        capture = Capture(capture_id=uuid4(), requested_url='https://example.test/',
                          captured_at=None, timestamp_precision='unknown', completeness='unavailable')
        client = Mock()
        material = MaterialStore(client)
        row = {'id': str(capture.capture_id), 'digest': capture.digest, 'document': 'a' * 64}
        client.query.return_value = {'data': [row]}
        material.digests = Mock(return_value={})
        self.assertEqual(material.complete_many([capture]), set())
        material.digests.return_value = {'a' * 64: 'b' * 64}
        self.assertEqual(material.complete_many([capture]), {capture.capture_id})
        client.query.return_value = {'data': [row, row]}
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            material.complete_many([capture])
        client.query.return_value = {'data': [{**row, 'digest': '0' * 64}]}
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            material.complete_many([capture])

    def test_group_read_claim_releases_before_projection_and_retirement_wins(self):
        with TemporaryDirectory() as root:
            store = FileObjectStore(Path(root))
            captures = []
            for index in range(2):
                identity, observed = uuid4(), datetime.now(UTC)
                body = RawHtmlRepository(store).put(f'<p>body {index}</p>',
                    source_url='https://example.test/', visit_id=identity,
                    observed_at=observed, content_type='text/html')
                captures.append(Capture(capture_id=identity, requested_url='https://example.test/',
                    captured_at=observed, timestamp_precision='microsecond', completeness='complete',
                    payload=Payload(content_id=body.sha256, byte_length=body.size_bytes,
                        stored_bytes=body.compressed_size_bytes, object_key=body.object_key,
                        storage_encoding='zstd', representation='rendered_html', media_type='text/html', charset='utf-8')))
            retired, active, claims, written = set(), [], [], {}
            archive = SimpleNamespace(store=store, retired=lambda identity: identity in retired,
                                      location=lambda capture: 'raw/example#0')
            material = MaterialStore(Mock())
            material.complete_many = lambda values: {c.capture_id for c in values if str(c.capture_id) in written}
            material.digests = lambda *args: {}
            material.content = Mock(side_effect=AssertionError('Known missing document should not be fetched'))
            def insert(table, rows):
                self.assertTrue(active)
                if table == 'captures':
                    written.update(rows)
            material._insert_verified = insert
            project = material.project
            def projecting(capture, *args):
                self.assertFalse(active, 'Parsing must not hold distributed ownership')
                result = project(capture, *args)
                if capture == captures[0]:
                    retired.add(capture.capture_id)
                return result
            material.project = projecting
            @contextmanager
            def claim(identities):
                claims.append(identities)
                active.append(identities)
                try:
                    yield
                finally:
                    active.pop()
            with patch('periplus.materialization.storage.write_claims', claim):
                self.assertEqual(material.materialize_many(captures, archive), 2)
            self.assertEqual(len(claims), 2)
            self.assertEqual(set(claims[0]['content']), {c.payload.content_id for c in captures})
            self.assertNotIn(str(captures[0].capture_id), written)
            self.assertIn(str(captures[1].capture_id), written)

    def test_declared_oversize_is_rejected_before_object_read(self):
        capture = SimpleNamespace(payload=SimpleNamespace(byte_length=100, content_id='a' * 64))
        archive = Mock()
        with patch('periplus.materialization.storage.MAX_INPUT_BYTES', 99):
            with self.assertRaisesRegex(ValueError, 'limit is 99'):
                MaterialStore._read_source(capture, archive)
        archive.store.open.assert_not_called()
