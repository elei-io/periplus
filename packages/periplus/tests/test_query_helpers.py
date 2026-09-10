from importlib.resources import files
import json
from pathlib import Path
import tomllib
import unittest

import duckdb

from periplus.materialization.dom.encoder import parse_html
from periplus.materialization.dom.nodes import parse_document
from periplus.platform.catalogue.helpers import HELPERS
from periplus.platform.catalogue.helpers.subtree_text import SUBTREE_TEXT
from periplus.query.helpers import query_helpers, safe_helper_error
from periplus.query.validation import _bounded_query


class SubtreeTextTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.db.execute("CREATE SCHEMA public_v1")
        self.db.execute("CREATE TABLE public_v1.html_node(content_id VARCHAR, node_index INTEGER, subtree_end_index INTEGER, node_type VARCHAR, value VARCHAR)")
        self.db.execute("CREATE TABLE public_v1.html_element(content_id VARCHAR, node_index INTEGER, subtree_end_index INTEGER, depth INTEGER, tag VARCHAR, text_direct VARCHAR, text_tail VARCHAR)")
        for helper in (SUBTREE_TEXT,):
            self.db.execute(files('periplus.platform.catalogue').joinpath('sql', helper.schema, helper.resource).read_text())

    def tearDown(self):
        self.db.close()

    def load(self, source, content_id='a'):
        nodes, rows = parse_document(source)
        self.db.executemany("INSERT INTO public_v1.html_node VALUES (?,?,?,?,?)", [(content_id,n.node_index,n.subtree_end_index,n.node_type,n.value) for n in nodes])
        self.db.executemany("INSERT INTO public_v1.html_element VALUES (?,?,?,?,?,?,?)", [
            (content_id, r.element_index, r.subtree_end_index, r.depth, r.tag, r.text_direct, r.text_tail) for r in rows
        ])
        return rows

    def test_every_subtree_matches_parser_text_order_and_excludes_root_tail(self):
        source = '<main>A<p><a><code><span>dropna()</span></code></a> drops rows.</p>B<div>C<b>D<i>E</i>F</b>G</div>H<br>I</main>'
        rows = self.load(source)
        elements = list(parse_html(source).iter())
        self.assertEqual(len(elements), len(rows))
        for row, element in zip(rows, elements):
            with self.subTest(index=row.element_index):
                expected = ''.join(element.itertext())
                result = self.db.execute(_bounded_query("SELECT * FROM public_v1.subtree_text(?, ?)"), ['a', row.element_index]).fetchone()
                self.assertEqual(result, (expected, False, len(expected), row.subtree_end_index-row.element_index))

    def test_whitespace_unicode_empty_missing_and_truncation(self):
        rows = self.load('<pre>if x:\n\t<span>print("日本😀")</span>\n  end\n</pre>OUT<p></p>')
        root = next(r for r in rows if r.tag == 'pre')
        expected = 'if x:\n\tprint("日本😀")\n  end\n'
        for limit in (0, 1, 15, len(expected), len(expected)+1):
            result = self.db.execute('SELECT * FROM public_v1.subtree_text(?, ?, max_chars := ?)', ['a', root.element_index, limit]).fetchone()
            self.assertEqual(result[:3], (expected[:limit], limit < len(expected), len(expected)))
        empty = next(r for r in rows if r.tag == 'p')
        self.assertEqual(self.db.execute('SELECT text,truncated,total_chars FROM public_v1.subtree_text(?,?)', ['a',empty.element_index]).fetchone(), ('',False,0))
        self.assertEqual(self.db.execute("SELECT * FROM public_v1.subtree_text('missing',0)").fetchall(), [])
        self.assertEqual(self.db.execute("SELECT * FROM public_v1.subtree_text('a',99999)").fetchall(), [])
        self.assertEqual(self.db.execute("SELECT * FROM public_v1.subtree_text(NULL,0)").fetchall(), [])

    def test_limits_reject_oversize_subtrees_and_invalid_values(self):
        self.load('<p>one<b>two</b>three</p>')
        for suffix in ('max_chars := -1', 'max_chars := 100001', 'max_chars := NULL', 'max_chars := 1.5', 'max_nodes := 0', 'max_nodes := 10001', 'max_nodes := NULL', 'max_nodes := 1.5', 'max_nodes := 1'):
            with self.subTest(suffix=suffix), self.assertRaises(duckdb.InvalidInputException):
                self.db.execute(f"SELECT * FROM public_v1.subtree_text('a',0,{suffix})").fetchall()
        with self.assertRaises(duckdb.InvalidInputException):
            self.db.execute("SELECT * FROM public_v1.subtree_text('a',-1)").fetchall()

    def test_lateral_calls_keep_content_roots_and_limits_independent(self):
        self.load('<p>A<b>B</b>C</p>', 'a')
        self.load('<p>XYZ</p>', 'b')
        result = self.db.execute("""WITH roots(id, idx, cap) AS (VALUES ('a',0,2),('a',0,20),('b',0,1))
            SELECT r.id,r.cap,t.text,t.total_chars FROM roots r,
            LATERAL public_v1.subtree_text(r.id,r.idx,max_chars := r.cap) t ORDER BY r.id,r.cap""").fetchall()
        self.assertEqual(result, [('a',2,'AB',3),('a',20,'ABC',3),('b',1,'X',3)])

    def test_bulk_lateral_calls_match_baseline_and_bound_shared_nodes(self):
        a = self.load('<main><a>A<b>日本</b>C</a><a></a><a>third</a></main>', 'a')
        b = self.load('<main><a>other<script>raw</script><!--comment--></a></main>', 'b')
        anchors = [('a', row) for row in a if row.tag == 'a'] + [('b', row) for row in b if row.tag == 'a']
        selected_nodes = self.db.execute('SELECT count(*) FROM public_v1.html_node').fetchone()[0]
        self.db.execute("INSERT INTO public_v1.html_node SELECT 'unrelated', i::INTEGER, (i+1)::INTEGER, 'text', 'unrelated' FROM range(100000) r(i)")
        self.db.execute('CREATE TABLE roots(seq INTEGER, id VARCHAR, idx INTEGER, cap INTEGER)')
        refs = [(index, content, row.element_index, index % 3) for index, (content, row) in enumerate(anchors)]
        refs += [(10, 'a', anchors[0][1].element_index, 100), (11, 'a', anchors[0][1].element_index, 100),
                 (12, 'missing', 0, 100), (13, None, 0, 100), (14, 'a', 99999, 100)]
        self.db.executemany('INSERT INTO roots VALUES (?,?,?,?)', refs)
        baseline = Path(__file__).resolve().parents[3] / 'docs/query-investigations/anchor-subtree/baseline.sql'
        self.db.execute(baseline.read_text().replace('public_v1.subtree_text', 'public_v1.baseline_subtree'))
        query = """SELECT r.seq, r.id, r.idx, t.* FROM roots r
            LEFT JOIN LATERAL public_v1.subtree_text(r.id,r.idx,max_chars := r.cap) t ON TRUE
            ORDER BY r.seq"""
        expected = self.db.execute(query.replace('public_v1.subtree_text', 'public_v1.baseline_subtree')).fetchall()
        expected_types = self.db.description
        actual = self.db.execute(query).fetchall()
        self.assertEqual(self.db.description, expected_types)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), len(refs))
        profile = json.loads(self.db.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + query).fetchone()[1])
        def producers(node):
            if node.get('extra_info', {}).get('CTE Name') == 'page_nodes':
                yield node['children'][0]['operator_cardinality']
            for child in node.get('children', []):
                yield from producers(child)
        cardinalities = list(producers(profile))
        self.assertEqual(len(cardinalities), 1)
        # DuckDB may repeat the scoped input for distinct lateral bindings,
        # but unrelated corpus nodes must not reach that materialization.
        self.assertLessEqual(sum(cardinalities), selected_nodes * len(refs))

    def test_registry_documentation_and_examples_are_executable(self):
        rows = self.load('<pre>x = 1\n</pre>')
        docs = query_helpers()
        self.assertEqual(len(docs.helpers), len(HELPERS))
        for helper, doc in zip(HELPERS, docs.helpers):
            self.assertTrue(doc.description and doc.notes and doc.examples)
            self.assertEqual([c.name for c in doc.columns], list(helper.columns))
            if helper != SUBTREE_TEXT:
                continue
            self.db.execute(_bounded_query(doc.examples[0]), ['a',rows[0].element_index]).fetchall()
            self.db.execute(_bounded_query(doc.examples[1]), ['a']).fetchall()


class HelperPackagingTests(unittest.TestCase):
    def test_registered_sql_is_included_in_built_package(self):
        package_root = Path(str(files("periplus.platform.catalogue")))
        project = Path(__file__).resolve().parents[1]
        config = tomllib.loads((project / "pyproject.toml").read_text())
        patterns = config["tool"]["setuptools"]["package-data"]["periplus.platform.catalogue"]
        included = {path.resolve() for pattern in patterns for path in package_root.glob(pattern)}
        for helper in HELPERS:
            resource = package_root / "sql" / helper.schema / helper.resource
            self.assertIn(resource.resolve(), included)
            for error in helper.errors:
                self.assertIn("error('" + error + "')", resource.read_text())
                self.assertEqual(safe_helper_error("Invalid Input Error: " + error), error)
        self.assertIsNone(safe_helper_error("IO Error: secret storage URL"))
        self.assertIsNone(safe_helper_error("Invalid Input Error: arbitrary client error"))
