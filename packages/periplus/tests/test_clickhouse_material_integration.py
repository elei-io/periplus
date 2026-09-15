"""Opt-in publication tests against disposable local ClickHouse/Postgres."""
import os
import asyncio
from uuid import uuid4
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_ingestion_evidence import _visit_evidence
from periplus.ingestion.objects.store import FileObjectStore
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.ingestion.queue import visit_ingestion_job
from periplus.ingestion.service import RepositoryIngestor
from periplus.ingestion.storage import EvidenceStore
from periplus.materialization.storage import MaterialStore, build_material
from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.clickhouse import connect_clickhouse


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_CLICKHOUSE') == '1', 'requires disposable ClickHouse/Postgres')
class MaterialPublicationTests(unittest.TestCase):
    def test_partial_output_and_missing_base_are_hidden_then_replay_publishes_once(self):
        client = connect_clickhouse()
        self.addCleanup(client.close)
        evidence = _visit_evidence()
        with TemporaryDirectory() as directory:
            objects = FileObjectStore(Path(directory))
            html = RawHtmlRepository(objects)
            stored = html.put(f'<h1>猫😀</h1><a href="/{evidence.visit.visit_id}">link</a>',
                source_url=evidence.visit.requested_url, visit_id=evidence.visit.visit_id,
                observed_at=evidence.visit.observed_at, content_type='text/html')
            evidence = evidence.model_copy(update={'document': evidence.document.model_copy(update={
                'content_sha256': stored.sha256, 'content_bytes': stored.size_bytes,
                'object_key': stored.object_key, 'stored_bytes': stored.compressed_size_bytes,
            })})
            ingestor = RepositoryIngestor(html_repository=html, document_repository=ExactDocumentRepository(objects),
                                         evidence_store=EvidenceStore(client))
            content, visit = build_material(evidence, ingestor)
            store = MaterialStore(client)
            original = client.insert_json
            def reject_final(table, row):
                if table == 'material.visit_results':
                    raise CatalogueConflictError('injected rejection before final publication')
                return original(table, row)
            with patch.object(client, 'insert_json', side_effect=reject_final):
                with self.assertRaises(CatalogueConflictError):
                    store.publish(content, visit)
            def count(table, column, value):
                return client.query(f'SELECT count() AS n FROM {table} WHERE toString({column})={{id:String}}',
                                    parameters={'id': str(value)})['data'][0]['n']
            self.assertEqual(count('public_v1.html_element', 'content_id', stored.sha256), 0)
            self.assertEqual(count('material.visit_results', 'visit_id', visit.visit_id), 0)
            asyncio.run(crash_before_ack_and_redeliver(content, visit, store))
            self.assertFalse(store.publish(content, visit))
            self.assertEqual(count('public_v1.capture', 'capture_id', visit.visit_id), 0)
            job = visit_ingestion_job(evidence)
            ingestor.commit_prepared_batch([ingestor.prepare(job)])
            self.assertEqual(count('public_v1.capture', 'capture_id', visit.visit_id), 1)
            self.assertEqual(count('public_v1.link', 'capture_id', visit.visit_id), 1)
            rows = client.query("SELECT text FROM public_v1.html_element WHERE content_id={id:String} AND tag='h1'",
                                parameters={'id': stored.sha256})['data']
            self.assertEqual(rows, [{'text': '猫😀'}])
            self.assertEqual(count('material.visit_results', 'visit_id', visit.visit_id), 1)


async def crash_before_ack_and_redeliver(content, visit, store):
    """Use a disposable real JetStream lane; never touch application consumers."""
    import nats
    from nats.js.api import ConsumerConfig, AckPolicy, StorageType
    from periplus.platform.config import get_str
    name = 'CRASH_TEST_' + uuid4().hex
    subject = 'test.material.' + uuid4().hex
    nc = await nats.connect(get_str('PERIPLUS_NATS_URL'))
    js = nc.jetstream()
    try:
        await js.add_stream(name=name, subjects=[subject], storage=StorageType.FILE)
        await js.add_consumer(name, ConsumerConfig(durable_name='writer',
            filter_subject=subject, ack_policy=AckPolicy.EXPLICIT, ack_wait=1))
        await js.publish(subject, json.dumps({'content': content.model_dump(mode='json'),
                                            'visit': visit.model_dump(mode='json')}).encode())
        child = await asyncio.create_subprocess_exec(sys.executable, '-c', """
import asyncio, json, os, sys, nats
from periplus.materialization.html_content import HtmlContent
from periplus.materialization.storage import MaterialStore, VisitMaterial
from periplus.platform.clickhouse import connect_clickhouse
from periplus.platform.config import get_str
async def main():
    nc = await nats.connect(get_str('PERIPLUS_NATS_URL'))
    sub = await nc.jetstream().pull_subscribe(sys.argv[2], durable='writer', stream=sys.argv[1])
    message, = await sub.fetch(1, timeout=10)
    payload = json.loads(message.data)
    created = MaterialStore(connect_clickhouse()).publish(HtmlContent.model_validate(payload['content']),
                                                         VisitMaterial.model_validate(payload['visit']))
    assert created
    os._exit(23)
asyncio.run(main())
""", name, subject, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, stderr = await asyncio.wait_for(child.communicate(), timeout=60)
        except BaseException:
            if child.returncode is None:
                child.kill()
                await child.wait()
            raise
        if child.returncode != 23:
            raise AssertionError(f'Writer did not reach its post-commit crash: {stderr.decode()}')
        sub = await js.pull_subscribe(subject, durable='writer', stream=name)
        message, = await sub.fetch(1, timeout=10)
        if message.metadata.num_delivered < 2:
            raise AssertionError('Expected redelivery of the unacknowledged message')
        if await asyncio.to_thread(store.publish, content, visit):
            raise AssertionError('Redelivery duplicated durable material output')
        await message.ack_sync()
        state = await js.consumer_info(name, 'writer')
        if state.num_ack_pending != 0 or state.num_pending != 0:
            raise AssertionError('Reconciled delivery was not fully acknowledged')
    finally:
        await js.delete_stream(name)
        await nc.close()


if __name__ == '__main__':
    unittest.main()
