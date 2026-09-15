"""Recovery inputs survive missing queues, duplicate publication and retirement."""
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import UUID, uuid4
from unittest.mock import patch

from periplus.ingestion.archive import Archive, ArchiveConflict, CaptureRetired, capture_key, event_key
from periplus.ingestion.captures import Capture, Payload
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.store import FileObjectStore
from periplus.ingestion.objects.exceptions import RepositoryObjectNotFound, RepositoryIntegrityError


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        directory=TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store=FileObjectStore(Path(directory.name))
        self.archive=Archive(self.store)

    def capture(self,identity=None,text='<h1>Hello</h1>'):
        identity=identity or uuid4()
        now=datetime.now(UTC)
        stored=RawHtmlRepository(self.store).put(text,source_url='https://example.com/',visit_id=identity,observed_at=now,content_type='text/html')
        return Capture(capture_id=identity,requested_url='https://example.com/',effective_url='https://example.com/',captured_at=now,
            timestamp_precision='microsecond',http_status=200,completeness='complete',payload=Payload(
            content_id=stored.sha256,byte_length=stored.size_bytes,object_key=stored.object_key,storage_encoding='zstd',
            stored_bytes=stored.compressed_size_bytes,representation='rendered_html',media_type='text/html',charset='utf-8'))

    def test_concurrent_append_is_contiguous_and_recoverable_without_head_database(self):
        captures=[self.capture(UUID(int=i*16)) for i in range(1,25)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            events=list(pool.map(lambda c:Archive(self.store).commit(c),captures))
        fresh=Archive(self.store)
        self.assertEqual(fresh.head(0),24)
        self.assertEqual({e.sequence for e in events},set(range(1,25)))
        for event in fresh.events(fresh.heads()):fresh.verify_payload(fresh.read(event.capture_id,event.digest))

    def test_duplicate_and_lost_commit_reply_are_safe(self):
        capture=self.capture()
        original=self.archive._put
        lost=False
        def write(key,value):
            nonlocal lost
            result=original(key,value)
            if '/journal/' in key and not lost:
                lost=True
                raise ConnectionError('Reply lost after durable creation')
            return result
        with patch.object(self.archive,'_put',side_effect=write):
            with self.assertRaises(ConnectionError):self.archive.commit(capture)
        event=Archive(self.store).commit(capture)
        self.assertEqual(Archive(self.store).commit(capture),event)
        self.assertEqual(len(list(self.archive.events(self.archive.heads()))),2)
        self.assertEqual(self.archive.read(capture.capture_id),capture)

    def test_conflict_cannot_replace_existing_capture(self):
        capture=self.capture()
        self.archive.commit(capture)
        with self.assertRaises(ArchiveConflict):self.archive.commit(capture.model_copy(update={'effective_url':'https://different.example/'}))
        self.assertEqual(self.archive.read(capture.capture_id),capture)

    def test_retirement_survives_missing_notification_and_rejects_reimport(self):
        capture=self.capture()
        self.archive.commit(capture)
        with patch.object(self.archive,'_append',side_effect=ConnectionError('notification lost')):
            with self.assertRaises(ConnectionError):self.archive.retire(capture.capture_id)
        fresh=Archive(self.store)
        self.assertTrue(fresh.retired(capture.capture_id))
        with self.assertRaises(CaptureRetired):fresh.commit(capture)
        self.assertEqual(fresh.retire(capture.capture_id).kind,'retirement')

    def test_manifest_detects_missing_tail_and_corrupt_body(self):
        capture=self.capture()
        event=self.archive.commit(capture)
        key,manifest=self.archive.manifest('a'*64,'raw/corpus/v1/software/'+'b'*64+'.tar')
        self.store.delete(event_key(event.shard,event.sequence))
        with self.assertRaises(RepositoryObjectNotFound):list(Archive(self.store).events(self.archive.read_manifest(key).heads))
        self.store.delete(capture.payload.object_key)
        with self.assertRaises(RepositoryIntegrityError):self.archive.verify_payload(capture)

    def test_operational_fields_are_not_archived(self):
        capture=self.capture()
        self.archive.commit(capture)
        self.assertEqual(set(self.archive.read(capture.capture_id).model_dump()),
            {'format_version','capture_id','requested_url','effective_url','captured_at','timestamp_precision','http_status','completeness','payload','source'})
