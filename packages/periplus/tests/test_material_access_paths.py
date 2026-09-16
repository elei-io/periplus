"""Native layout correctness, publication fencing and retry proof on isolated local tables."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from dotenv import dotenv_values
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.html_content import html_content
from periplus.materialization.element_rows import encode_element, insert_elements
from periplus.materialization.storage import MaterialStore, install_material_schema, output_row
from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig
from periplus.platform.clickhouse.public import install_public_schema


def document(identity='a' * 64):
    source = '''<main class="usa-header usa-header--basic">猫<strong>😀 snow</strong>!</main>
    <p class="hp-icon">alpha</p><p class="beta">beta</p>
    <svg viewBox="0 0 126.719 115.379"></svg>
    <script type="application/ld+json">{"@type":["Product"],"name":"Blueair 3450i"}</script>'''
    nodes, elements = parse_document(source)
    parsed = html_content(identity, nodes, elements)
    parsed.pop('content_sha256')
    return output_row(dict(document_id=identity, content_id=identity, representation='rendered_html', encoding='utf-8', **parsed))


class ElementWireTests(unittest.TestCase):
    def test_character_offsets_and_map_order_do_not_change_digest(self):
        doc = document()
        main = next(e for e in doc['elements'] if e['tag'] == 'main')
        self.assertEqual(doc['document_text'][main['text_start']:main['text_end']], '猫😀 snow!')
        first = encode_element(doc, main)
        self.assertIn('猫😀 snow!'.encode(), first.wire)
        changed = {**main, 'attributes': dict(reversed(list(main['attributes'].items())))}
        self.assertEqual(first, encode_element(doc, changed))


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_ACCESS_PATHS') == '1', 'requires local Compose ClickHouse')
class NativeAccessPathsTests(unittest.TestCase):
    def setUp(self):
        env = dotenv_values(Path(__file__).resolve().parents[3] / '.env')
        self.client = ClickHouseClient(ClickHouseConfig(
            url='http://127.0.0.1:' + str(env.get('PERIPLUS_CLICKHOUSE_HTTP_PORT') or '8123'),
            username=env.get('PERIPLUS_CLICKHOUSE_USER') or 'periplus',
            password=env.get('PERIPLUS_CLICKHOUSE_PASSWORD') or 'clickhouse_local'))
        identity = uuid4().hex
        self.database, self.public = 'material_' + identity, 'query_' + identity
        self.addCleanup(self.client.close)
        self.addCleanup(lambda: self.client.execute(f'DROP DATABASE IF EXISTS {self.database} SYNC'))
        self.addCleanup(lambda: self.client.execute(f'DROP DATABASE IF EXISTS {self.public} SYNC'))
        install_material_schema(self.client, self.database)
        install_public_schema(self.client, self.database, self.public)
        self.material = MaterialStore(self.client, self.database)
        self.doc = document()

    def query(self, sql):
        return self.client.query(sql.replace('public_v1.', self.public + '.'))['data']

    def publish_capture(self, identity=None):
        identity = identity or str(uuid4())
        self.client.execute(f"INSERT INTO {self.database}.captures (capture_id,document_id,completeness,url) "
                            "VALUES ({capture:UUID},{document:String},'complete','https://example.test/')",
                            parameters={'capture': identity, 'document': self.doc['document_id']})
        return identity

    def test_partial_write_lost_response_retry_and_public_semantics(self):
        real_execute = self.client.execute
        inserts = 0
        def interrupted(sql, **kwargs):
            nonlocal inserts
            result = real_execute(sql, **kwargs)
            if sql.startswith('INSERT INTO') and '.html_elements ' in sql:
                inserts += 1
                if inserts == 1:
                    raise OSError('lost successful insert response')
            return result
        # An already-present capture cannot expose a partial element set either.
        self.publish_capture()
        with patch.object(self.client, 'execute', interrupted), patch('periplus.materialization.element_rows.MAX_BLOCK_ROWS', 3):
            with self.assertRaisesRegex(OSError, 'lost successful'):
                self.material._insert_verified('html_documents', {self.doc['document_id']: self.doc})
        self.assertEqual(self.query('SELECT count() AS n FROM public_v1.html_element'), [{'n': 0}])
        self.material._insert_verified('html_documents', {self.doc['document_id']: self.doc})
        self.material._insert_verified('html_documents', {self.doc['document_id']: self.doc})
        self.assertEqual(self.query('SELECT count() AS n FROM public_v1.html_element'), [{'n': len(self.doc['elements'])}])
        rows = self.query('SELECT * FROM public_v1.html_element ORDER BY node_index')
        for found, original in zip(rows, self.doc['elements'], strict=True):
            self.assertEqual(found['text'], self.doc['document_text'][original['text_start']:original['text_end']])
            for key in ('node_index','parent_index','subtree_end_index','sibling_index','depth','tag','namespace','attributes','text_direct'):
                self.assertEqual(found[key], original[key])
        self.assertEqual(self.material.content(self.doc['document_id']), dict(self.doc))
        self.assertEqual(self.query("SELECT name, types FROM public_v1.html_json_ld WHERE name='Blueair 3450i' AND has(types,'Product')"),
                         [{'name': 'Blueair 3450i', 'types': ['Product']}])
        self.assertEqual(self.query("SELECT text FROM public_v1.html_element WHERE hasAll(splitByWhitespace(attributes['class']), ['usa-header','usa-header--basic']) SETTINGS optimize_functions_to_subcolumns=0"), [{'text':'猫😀 snow!'}])
        self.assertEqual(self.query("SELECT count() AS n FROM public_v1.html_element WHERE hasAll(splitByWhitespace(attributes['class']), ['hp-icon','beta']) SETTINGS optimize_functions_to_subcolumns=0"), [{'n':0}])
        self.assertEqual(self.query("SELECT count() AS n FROM public_v1.capture WHERE url='https://example.test/'"), [{'n':1}])
        plan = self.client.query(f"EXPLAIN indexes=1 SELECT text FROM {self.public}.html_element WHERE has(splitByWhitespace(attributes['class']),'hp-icon') SETTINGS optimize_functions_to_subcolumns=0")
        self.assertIn('classes', str(plan))
        for sql, index in (
            ("SELECT tag FROM {db}.html_element WHERE attributes['viewBox']='0 0 126.719 115.379'", 'attribute_values'),
            ("SELECT name FROM {db}.html_json_ld WHERE name='Blueair 3450i'", 'name_exact'),
            ("SELECT url FROM {db}.capture WHERE url='https://example.test/'", 'url_exact'),
            ("SELECT document_id FROM {db}.capture WHERE hasAllTokens(lower(text), ['snow'])", 'words'),
        ):
            plan = self.client.query('EXPLAIN indexes=1 '+sql.format(db=self.public)+' SETTINGS optimize_functions_to_subcolumns=0')
            self.assertIn(index, str(plan))

    def test_conflicting_partial_output_never_publishes(self):
        insert_elements(self.client, self.database, [self.doc])
        other = dict(self.doc)
        other.pop('output_digest')
        other['document_text'] = 'x' * len(other['document_text'])
        other = output_row(other)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            self.material._insert_verified('html_documents', {self.doc['document_id']: other})
        self.assertEqual(self.material.digests('html_documents', [self.doc['document_id']]), {})

    def test_retirement_reclaims_unpublished_partial_output(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        insert_elements(self.client, self.database, [self.doc])
        capture = SimpleNamespace(capture_id=uuid4(), payload=SimpleNamespace(document_id=self.doc['document_id'],content_id=self.doc['content_id']))
        archive = SimpleNamespace(retired=lambda identity: True)
        with patch('periplus.materialization.storage.write_claims', lambda _: nullcontext()):
            self.material.retire(capture, archive)
        result = self.client.query(f'SELECT count() AS n FROM {self.database}.html_elements')['data']
        self.assertEqual(result, [{'n':0}])

    def test_actual_query_service_uses_native_indexes_without_rewriting_predicates(self):
        from datetime import UTC, datetime, timedelta
        from periplus.platform.clickhouse.public import install_query_user
        from periplus.query.binding import PublicationBinding
        from periplus.query.models import QueryRequest
        from periplus.query.service import QueryService
        self.material._insert_verified('html_documents', {self.doc['document_id']: self.doc})
        self.publish_capture()
        username = 'access_reader_' + uuid4().hex
        env = dotenv_values(Path(__file__).resolve().parents[3] / '.env')
        config = ClickHouseConfig(url='http://127.0.0.1:' + str(env.get('PERIPLUS_CLICKHOUSE_HTTP_PORT') or '8123'),
                                 username=username, password=uuid4().hex, query_only=True)
        self.addCleanup(lambda: self.client.execute(f'DROP USER IF EXISTS {username}'))
        with patch('periplus.platform.clickhouse.client.ClickHouseConfig.for_query', return_value=config), patch('periplus.platform.config.get_str', return_value='default'):
            install_query_user(self.client)
        self.client.execute(f'GRANT SELECT ON {self.public}.* TO {username}')
        service = QueryService(config)
        self.addCleanup(service.close)
        binding = PublicationBinding(database=self.public, revision=1, expires_at=datetime.now(UTC)+timedelta(seconds=30))
        cases = [
            ("SELECT text FROM public_v1.html_element WHERE has(splitByWhitespace(attributes['class']),'hp-icon')", [['alpha']]),
            ("SELECT tag FROM public_v1.html_element WHERE attributes['viewBox']='0 0 126.719 115.379'", [['svg']]),
            ("SELECT name FROM public_v1.html_json_ld WHERE has(types,'Product') AND name='Blueair 3450i'", [['Blueair 3450i']]),
            ("SELECT url FROM public_v1.capture WHERE url='https://example.test/'", [['https://example.test/']]),
            ('SELECT count(*) FROM public_v1.capture', [[1]]),
        ]
        for sql, expected in cases:
            with self.subTest(sql=sql):
                result = service.execute(QueryRequest(sql=sql), publication=binding)
                self.assertEqual(result.rows, expected)
        with self.assertRaises(ValueError):
            service.execute(QueryRequest(sql=f'SELECT * FROM {self.database}.html_elements'), publication=binding)

    def test_archive_pipeline_shared_document_links_and_last_reference_retirement(self):
        from contextlib import nullcontext
        from datetime import UTC, datetime
        from tempfile import TemporaryDirectory
        from periplus.ingestion.archive import Archive
        from periplus.ingestion.captures import Capture, Payload
        from periplus.ingestion.objects.html import RawHtmlRepository
        from periplus.ingestion.objects.store import FileObjectStore
        with TemporaryDirectory() as root, patch('periplus.materialization.storage.write_claims', lambda _: nullcontext()):
            archive = Archive(FileObjectStore(Path(root)))
            now = datetime.now(UTC)
            raw = RawHtmlRepository(archive.store).put('<p>猫<a href="next">go</a>😀</p>', source_url='https://example.test/a/',
                visit_id=uuid4(), observed_at=now, content_type='text/html')
            payload = Payload(content_id=raw.sha256, byte_length=raw.size_bytes, stored_bytes=raw.compressed_size_bytes,
                object_key=raw.object_key, storage_encoding='zstd', representation='rendered_html', media_type='text/html', charset='utf-8')
            captures = [Capture(capture_id=uuid4(),requested_url='https://example.test/'+path+'/',captured_at=now,
                timestamp_precision='microsecond',http_status=200,completeness='complete',payload=payload) for path in ('a','b')]
            archive.commit_many(captures)
            # Separate deliveries force reuse through the stored document, not an in-process cache.
            for capture in captures:
                self.material.materialize_many([capture],archive)
            self.material.materialize_many(captures,archive)
            self.assertEqual(self.query('SELECT text, element_count FROM public_v1.capture ORDER BY url'),
                             [{'text': '猫go😀', 'element_count': len(parse_document('<p>猫<a href="next">go</a>😀</p>')[1])}] * 2)
            self.assertEqual(self.query('SELECT uniqExact(document_id) AS n FROM public_v1.capture'), [{'n':1}])
            self.assertEqual(self.query("SELECT l.target_url FROM public_v1.link l WHERE l.capture_id IN (SELECT capture_id FROM public_v1.capture WHERE url='https://example.test/b/')"), [{'target_url':'https://example.test/b/next'}])
            archive.retire(captures[0].capture_id)
            self.material.retire(captures[0],archive)
            self.assertEqual(self.query('SELECT uniqExact(document_id) AS n FROM public_v1.capture'), [{'n':1}])
            archive.retire(captures[1].capture_id)
            self.material.retire(captures[1],archive)
            for table in ('html_documents','html_elements','json_ld','captures'):
                self.assertEqual(self.client.query(f'SELECT count() AS n FROM {self.database}.{table}')['data'], [{'n':0}])

    def test_url_normalization_and_distinct_interpretations_of_identical_bytes(self):
        from contextlib import nullcontext
        from datetime import UTC, datetime
        from tempfile import TemporaryDirectory
        from periplus.ingestion.archive import Archive
        from periplus.ingestion.captures import Capture, Payload
        from periplus.ingestion.objects.html import RawHtmlRepository
        from periplus.ingestion.objects.store import FileObjectStore
        with TemporaryDirectory() as root, patch('periplus.materialization.storage.write_claims', lambda _: nullcontext()):
            archive = Archive(FileObjectStore(Path(root)))
            now = datetime.now(UTC)
            raw = RawHtmlRepository(archive.store).put('<p>é</p>', source_url='https://example.test/',
                visit_id=uuid4(), observed_at=now, content_type='text/html')
            captures = []
            cases = [('HTTP://EXAMPLE.COM:80?x=1#frag',None,'utf-8','http://example.com/?x=1','é'),
                     ('http://[::1]:80','HTTPS://EXAMPLE.COM:443/final#x','iso-8859-1','https://example.com/final','Ã©')]
            for requested,effective,charset,_,_ in cases:
                payload = Payload(content_id=raw.sha256, byte_length=raw.size_bytes, stored_bytes=raw.compressed_size_bytes,
                    object_key=raw.object_key, storage_encoding='zstd', representation='rendered_html', media_type='text/html', charset=charset)
                captures.append(Capture(capture_id=uuid4(),requested_url=requested,effective_url=effective,captured_at=now,
                    timestamp_precision='microsecond',http_status=200,completeness='complete',payload=payload))
            archive.commit_many(captures)
            self.material.materialize_many(captures,archive)
            self.assertNotEqual(captures[0].payload.document_id, captures[1].payload.document_id)
            rows = self.query("SELECT c.url,e.text AS text FROM public_v1.capture c JOIN public_v1.html_element e USING(document_id) WHERE e.tag='p' ORDER BY c.url")
            self.assertEqual(rows,[{'url':case[3],'text':case[4]} for case in cases])
            self.assertEqual(self.query('SELECT count() AS n FROM public_v1.capture WHERE url NOT IN (SELECT url FROM public_v1.page)'), [{'n':0}])

    def test_document_index_is_selected_for_raw_download_lookup(self):
        # Generated captures test optimizer selection, not corpus capacity or compression.
        self.client.execute(f"INSERT INTO {self.database}.captures (capture_id,document_id,url,object_key) "
            "SELECT generateUUIDv4(), leftPad(toString(number),64,'0'), 'https://projection.test/', 'body' FROM numbers(100000)")
        identity = str(50000).zfill(64)
        sql = f"SELECT object_key FROM {self.database}.captures WHERE document_id={{id:String}} LIMIT 1"
        plan = self.client.query('EXPLAIN indexes=1 '+sql, parameters={'id':identity})
        self.assertIn('document_exact', str(plan))
        self.assertEqual(self.client.query(sql,parameters={'id':identity})['data'], [{'object_key':'body'}])

    def test_large_row_batch_reaches_writer_without_small_part_fragmentation(self):
        # A generated structural fixture exercises the verification response bound.
        count = 100000
        doc = dict(self.doc)
        doc.pop('output_digest')
        doc['document_text'] = 'x' * count
        doc['elements'] = [dict(node_index=i,parent_index=None,subtree_end_index=i+1,sibling_index=i,
            depth=0,tag='p',namespace=None,attributes={},text_direct='x',text_start=i,text_end=i+1) for i in range(count)]
        doc = output_row(doc)
        execute = self.client.execute
        inserts = 0
        def counted(sql, **kwargs):
            nonlocal inserts
            if sql.startswith('INSERT INTO') and '.html_elements ' in sql:
                inserts += 1
            return execute(sql, **kwargs)
        with patch.object(self.client, 'execute', counted):
            self.material._insert_verified('html_documents', {doc['document_id']:doc})
        self.assertEqual(inserts,1)
        self.assertEqual(self.client.query(f'SELECT count() AS n,uniqExact(node_index) AS u FROM {self.database}.html_elements')['data'], [{'n':count,'u':count}])
