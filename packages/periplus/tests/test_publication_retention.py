"""Object publication interleavings and fail-closed marker recovery."""
from datetime import UTC, datetime, timedelta
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from periplus.ingestion.objects.store import FileObjectStore
from periplus.ingestion.objects.publication import (
    claim, release, claim_key, begin_reclamation, end_reclamation,
    ContentRetiring, RETIRING_PREFIX,
)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = FileObjectStore(Path(tmp.name))
        self.content = 'a'*64
        self.visit = uuid4()

    def test_publication_before_retirement_blocks_deletion(self):
        claim(self.store, self.content, self.visit)
        self.assertFalse(begin_reclamation(self.store, self.content))
        self.assertTrue(self.store.exists(claim_key(self.content, self.visit)))
        release(self.store, self.content, self.visit)
        self.assertTrue(begin_reclamation(self.store, self.content))
        end_reclamation(self.store, self.content)

    def test_retirement_before_publication_rejects_adoption(self):
        self.assertTrue(begin_reclamation(self.store, self.content))
        with self.assertRaises(ContentRetiring): claim(self.store, self.content, self.visit)
        self.assertFalse(self.store.exists(claim_key(self.content, self.visit)))
        end_reclamation(self.store, self.content)
        claim(self.store, self.content, self.visit)
        self.assertTrue(self.store.exists(claim_key(self.content, self.visit)))

    def test_publisher_between_marker_and_claim_check_stops_before_bytes(self):
        original = self.store.put_if_absent
        def put(key, content, **kwargs):
            result = original(key, content, **kwargs)
            if key == RETIRING_PREFIX + self.content:
                with self.assertRaises(ContentRetiring): claim(self.store, self.content, self.visit)
            return result
        self.store.put_if_absent = put
        self.assertTrue(begin_reclamation(self.store, self.content))

    def test_crashed_owner_marker_waits_beyond_worker_hard_timeout(self):
        self.assertTrue(begin_reclamation(self.store, self.content))
        self.assertFalse(begin_reclamation(self.store, self.content))
        with self.assertRaises(ContentRetiring): claim(self.store, self.content, self.visit)
        self.assertTrue(begin_reclamation(self.store, self.content, now=datetime.now(UTC)+timedelta(hours=2)))
        end_reclamation(self.store, self.content)

    def test_another_content_identity_is_independent(self):
        claim(self.store, self.content, self.visit)
        self.assertTrue(begin_reclamation(self.store, 'b'*64))
