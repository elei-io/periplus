"""Production projector agrees with independent page-span containment."""
from dataclasses import replace
import random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from uuid import uuid4

import duckdb
import pyarrow as pa

from periplus.materialization.registry import BY_NAME, registry_digest
from periplus.materialization.projections.html_terms import page_terms, validate_tokenizer
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.batch import _write_partitioned_parquet


class HtmlTermsTests(unittest.TestCase):
    def check_html(self, html):
        nodes, elements = parse_document(html)
        context = VisitBatchContext((), (), (), {'fixture': elements}, {'fixture': nodes}, {}, frozenset({'fixture'}))
        element_rows = BY_NAME['html_elements'].rows(context).to_pylist()
        root = next(row for row in element_rows if row['parent_index'] is None)
        words = list(page_terms(root['text']))
        expected = {}
        for term,lo,hi in words:
            for element in element_rows:
                if element['text_start'] <= lo and hi <= element['text_end']:
                    expected.setdefault(term, set()).add(element['node_index'])
        actual = BY_NAME['html_terms'].rows(context).to_pylist()
        self.assertEqual(actual, [{'term': term, 'content_sha256': 'fixture', 'node_indexes': sorted(indexes)} for term,indexes in sorted(expected.items())])
        self.assertEqual(BY_NAME['html_terms'].rows(replace(context,content_output_hashes=frozenset())).num_rows,0)
        return actual, element_rows

    def test_unicode_inline_boundaries_and_repetition(self):
        validate_tokenizer()
        self.assertEqual(list(page_terms('🐒 Straße cafe\u0301')), [('strasse', 2, 8), ('café', 9, 14)])
        rows, elements = self.check_html('<p>cat<span>fish</span> catfish cat <b>fish</b> 🐒 <i>Straße</i> cafe<span>\u0301</span></p>')
        span = next(row for row in elements if row['tag']=='span')
        self.assertNotIn(span['node_index'], next(r['node_indexes'] for r in rows if r['term']=='catfish'))

    def test_title_attributes_empty_and_templates(self):
        rows,_ = self.check_html('<title>Title </title><meta name="description" content="secret"><p>Body</p><template> nested <b>word</b></template><script>code</script>')
        self.assertIn('title', {r['term'] for r in rows})
        self.assertNotIn('secret', {r['term'] for r in rows})
        self.check_html(''); self.check_html('<p>🐒 !</p>')

    def test_random_nested_markup(self):
        rng = random.Random(714)
        for _ in range(50):
            html = ''.join(rng.choice(('<span>','</span>','<b>','</b>','cat','fish',' ','🐒','Straße','e','\u0301')) for _ in range(60))
            self.check_html(html)

    def test_row_group_setting_changes_digest_and_written_files(self):
        spec = BY_NAME['html_terms']
        self.assertNotEqual(registry_digest((spec,)), registry_digest((replace(spec,parquet_row_group_size=4096),)))
        table = pa.Table.from_pylist([{'term':'cat','content_sha256':str(i),'node_indexes':[1,3]} for i in range(5000)],schema=spec.arrow_schema)
        with TemporaryDirectory() as root, duckdb.connect() as db:
            files = _write_partitioned_parquet(SimpleNamespace(config=SimpleNamespace(data_path=root),trusted_connection=db),table,run_id=uuid4(),batch_id=uuid4(),table_name=spec.name)
            self.assertEqual(len(files),1)
            groups = db.execute('SELECT DISTINCT row_group_id,row_group_num_rows FROM parquet_metadata(?) ORDER BY row_group_id',[files[0].path]).fetchall()
            self.assertEqual([r[1] for r in groups],[2048,2048,904])
            self.assertEqual(db.execute('SELECT count(*) FROM read_parquet(?)',[files[0].path]).fetchone()[0],5000)
