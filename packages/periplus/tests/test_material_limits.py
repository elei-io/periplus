"""Size admission must retain deterministic output and useful failure evidence."""
import unittest
from unittest.mock import patch

from periplus.ingestion.captures import canonical
from periplus.materialization.storage import output_row
from hashlib import sha256


class MaterialLimitsTests(unittest.TestCase):
    def test_budget_counts_utf8_bytes_and_reports_identity(self):
        row = {"document_id": "document-123", "document_text": "å" * 16}
        size = len(canonical(row))
        with patch("periplus.materialization.storage.MAX_OUTPUT_BYTES", size - 1):
            with self.assertRaisesRegex(ValueError, f"document-123 is {size} bytes; limit is {size - 1}"):
                output_row(row)
        with patch("periplus.materialization.storage.MAX_OUTPUT_BYTES", size):
            self.assertEqual(output_row(row), {**row, "output_digest": sha256(canonical(row)).hexdigest()})
