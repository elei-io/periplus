"""Long transport errors must not prevent durable attempt evidence or recovery."""
from datetime import UTC, datetime
from uuid import uuid4
import unittest

from periplus.crawl.acquisition.evidence import attempt_records
from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence


class AttemptEvidenceTests(unittest.TestCase):
    def test_long_error_is_bounded_without_changing_attempt_identity(self):
        now = datetime.now(UTC)
        attempt = AcquisitionAttemptEvidence(attempt=1, started_at=now, completed_at=now,
            requested_url="https://example.com", outcome="failed", failure_code="transport_failed",
            failure_message="x" * 4096)
        identity = uuid4()
        record, = attempt_records(identity, (attempt,))
        self.assertEqual(record.failure_message, "x" * 2048)
        self.assertEqual(record.failure_code, "transport_failed")
        self.assertEqual(record.visit_id, identity)
        self.assertEqual(attempt.failure_message, "x" * 4096)
        without_message = attempt.model_copy(update={"failure_message": None})
        self.assertEqual(attempt_records(identity, (without_message,))[0].failure_message, "transport_failed")
