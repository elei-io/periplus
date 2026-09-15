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


class DocumentDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_lookup_verifies_raw_hash_not_document_hash(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from periplus.operations.api.catalogue import download
        with TemporaryDirectory() as directory:
            Path(directory, "input").write_bytes(b"raw bytes")
            results = SimpleNamespace(material_database=AsyncMock(return_value="material"), _read=AsyncMock(return_value={"data": [{
                "object_key": "input", "storage_encoding": "identity", "content_bytes": 9,
                "digest": sha256(b"raw bytes").hexdigest(),
            }]}))
            slot = asyncio.Semaphore(1)
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
                download_slot=slot, crawl_results=results, document_store=FileObjectStore(Path(directory)))))
            document_id = "a" * 64
            response = await download(document_id, request)
            self.assertIn("WHERE document_id=", results._read.call_args.args[0])
            self.assertEqual(results._read.call_args.args[1], {"digest": document_id})
            self.assertEqual(b"".join([chunk async for chunk in response.body_iterator]), b"raw bytes")
            await response.background()
            self.assertFalse(slot.locked())
            self.assertEqual(response.headers["etag"], '"'+document_id+'"')
