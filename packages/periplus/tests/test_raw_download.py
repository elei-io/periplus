"""No successful download can expose unverified or over-budget raw bytes."""
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from periplus.ingestion.objects.store import FileObjectStore
from periplus.operations.api.catalogue import _verified_file


class RawDownloadTests(unittest.TestCase):
    def test_verified_file_rewinds_and_rejects_corruption(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'input'
            path.write_bytes(b'raw bytes')
            store = FileObjectStore(Path(directory))
            row = {'object_key': 'input', 'storage_encoding': 'identity', 'content_bytes': 9,
                   'digest': sha256(b'raw bytes').hexdigest()}
            with _verified_file(store, row) as file:
                self.assertEqual(file.read(), b'raw bytes')
            path.write_bytes(b'bad bytes')
            with self.assertRaises(ValueError): _verified_file(store, row)
            path.write_bytes(b'raw bytes plus extra')
            with self.assertRaises(ValueError): _verified_file(store, row)
