import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

import duckdb
from fastapi import FastAPI
from fastapi.testclient import TestClient

from periplus.ingestion.documents_http import router, get_document_store
from periplus.ingestion.objects.store import FileObjectStore
from periplus.platform.catalogue.control import get_catalogue_control


class PublicContentTests(unittest.TestCase):
    def test_retained_hash_download_and_missing_evidence(self):
        with TemporaryDirectory() as directory, duckdb.connect() as db:
            store = FileObjectStore(Path(directory))
            body = b'<p>Exact &amp; source</p>'
            digest = hashlib.sha256(body).hexdigest()
            store.put_if_absent('documents/fixture', io.BytesIO(body))
            db.execute('CREATE SCHEMA ingest')
            db.execute('CREATE TABLE ingest.visits(visit_id UUID, document_id UUID)')
            db.execute('''CREATE TABLE ingest.documents(document_id UUID, visit_id UUID,
                detected_media_type VARCHAR, charset VARCHAR, content_sha256 VARCHAR,
                content_bytes BIGINT, object_key VARCHAR, storage_encoding VARCHAR)''')
            visit_id, document_id = uuid4(), uuid4()
            db.execute('INSERT INTO ingest.visits VALUES (?,?)', [visit_id, document_id])
            db.execute("INSERT INTO ingest.documents VALUES (?,?,'text/html','utf-8',?,?,'documents/fixture','identity')",
                       [document_id, visit_id, digest, len(body)])
            class Catalogue:
                def trusted_remote_rows(self, sql):
                    return db.execute(sql).fetchall()
            class Control:
                async def run(self, operation):
                    return operation(None, Catalogue())
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_catalogue_control] = lambda: Control()
            app.dependency_overrides[get_document_store] = lambda: store
            with TestClient(app) as client:
                response = client.get(f'/documents/by-content/{digest}/content')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, body)
                self.assertEqual(response.headers['etag'], f'"{digest}"')
                self.assertIn('attachment', response.headers['content-disposition'])
                self.assertNotIn(str(document_id), response.headers['content-disposition'])
                self.assertEqual(client.get('/documents/by-content/' + '0'*64 + '/content').status_code, 404)
                db.execute('DELETE FROM ingest.visits')
                self.assertEqual(client.get(f'/documents/by-content/{digest}/content').status_code, 404)
