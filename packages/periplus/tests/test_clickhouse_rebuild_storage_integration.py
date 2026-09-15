"""Actual batched material writes, parse reuse and interrupted publication."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_ingestion_evidence import _visit_evidence
from periplus.ingestion.objects.store import FileObjectStore
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.ingestion.service import RepositoryIngestor
from periplus.ingestion.storage import EvidenceStore
from periplus.materialization.storage import MaterialStore, install_material_schema
from periplus.materialization.dom.nodes import parse_document
from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.clickhouse import connect_clickhouse


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_CLICKHOUSE') == '1', 'requires disposable local stores')
class RebuildStorageTests(unittest.TestCase):
    def test_batched_replay_parses_distinct_content_once(self):
        client = connect_clickhouse()
        target = 'material_' + uuid4().hex
        try:
            install_material_schema(client, target)
            with TemporaryDirectory() as directory:
                objects = FileObjectStore(Path(directory))
                html = RawHtmlRepository(objects)
                values = []
                for _ in range(8):
                    value = _visit_evidence()
                    stored = html.put('<h1>Reusable</h1><a href="relative">link</a>',
                        source_url=value.visit.requested_url, visit_id=value.visit.visit_id,
                        observed_at=value.visit.observed_at, content_type='text/html')
                    values.append(value.model_copy(update={'document': value.document.model_copy(update={
                        'content_sha256': stored.sha256, 'content_bytes': stored.size_bytes,
                        'object_key': stored.object_key, 'stored_bytes': stored.compressed_size_bytes})}))
                ingestor = RepositoryIngestor(html_repository=html, document_repository=ExactDocumentRepository(objects), evidence_store=EvidenceStore(client))
                store = MaterialStore(client, target)
                native = client.insert_rows
                def fail_visit(table, rows):
                    if table.endswith('.visit_results'):
                        raise CatalogueConflictError('injected rejection after content commit')
                    return native(table, rows)
                with patch('periplus.materialization.storage.parse_document', wraps=parse_document) as parser:
                    with patch.object(client, 'insert_rows', side_effect=fail_visit):
                        with self.assertRaisesRegex(CatalogueConflictError, 'injected rejection'): store.materialize_many(values, ingestor)
                    self.assertEqual(client.query(f'SELECT count() AS n FROM {target}.html_documents')['data'][0]['n'], 1)
                    self.assertIsNotNone(store.content(stored.sha256))
                    with patch.object(client, 'insert_rows', wraps=native) as inserts:
                        store.materialize_many(values, ingestor)
                        store.materialize_many(values, ingestor)
                        nonempty = [call for call in inserts.call_args_list if call.args[1]]
                        self.assertEqual(len(nonempty), 1)
                        self.assertEqual(len(nonempty[0].args[1]), 8)
                    self.assertEqual(parser.call_count, 1)
                self.assertEqual(client.query(f'SELECT count() AS n FROM {target}.visit_results')['data'][0]['n'], 8)
                self.assertEqual(client.query(f'SELECT count() AS n FROM {target}.html_documents')['data'][0]['n'], 1)
        finally:
            client.execute(f'DROP DATABASE IF EXISTS {target}')
            client.close()
