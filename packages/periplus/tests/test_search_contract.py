"""Public positional search contract with real ICU, projections and SQL composition."""

import unittest
from importlib.resources import files

import duckdb

from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.query.search import bind_search


class SearchContractTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute("CREATE SCHEMA material; CREATE SCHEMA public_v1")
        self.pages = {
            "a": "<title>monkey zoo</title><p>mon<strong>key</strong> café 日本語 100% a_b monkey monkey</p>",
            "b": '<meta name="description" content="monkey description"><p>Something else</p>',
            "c": "<p>monkey body</p>",
            "d": "<title>Other</title><script>secretmonkey</script><style>stylemonkey</style><p>A<br>B</p><div></div>",
            "e": "<p>A <b>B C</b><!--omit--> D&nbsp;E</p><template>templateword</template>",
            "f": "<title>monkey</title><p>zoo</p>",
        }
        parsed = {k: parse_document(v) for k, v in self.pages.items()}
        context = VisitBatchContext(
            (),
            (),
            (),
            {k: v[1] for k, v in parsed.items()},
            {k: v[0] for k, v in parsed.items()},
            {},
            frozenset(parsed),
        )
        for name in ["html_nodes", "posting"]:
            self.db.register("rows", BY_NAME[name].rows(context))
            self.db.execute(f"CREATE TABLE material.{name} AS SELECT * FROM rows")
        self.db.execute("CREATE TABLE public_v1.capture(content_id VARCHAR)")
        self.db.executemany(
            "INSERT INTO public_v1.capture VALUES (?)",
            [(k,) for k in self.pages] + [("a",)],
        )
        root = files("periplus.platform.catalogue").joinpath("sql/public_v1")
        for resource in [
            "views/html_node.sql",
            "views/html_element.sql",
            "helpers/search.sql",
        ]:
            self.db.execute(root.joinpath(resource).read_text())

    def search(self, q, sql="SELECT * FROM search(?) ORDER BY score DESC,content_id"):
        b = bind_search(sql, [q], self.db, execute=True, check=lambda: None)
        return self.db.execute(b.sql, b.parameters).fetchall()

    def test_coverage_grain_frequency_and_no_attributes(self):
        rows = self.search("MONKEY")
        self.assertEqual(
            [(r[0], r[2]) for r in rows], [("a", 4.0), ("c", 1.0), ("f", 1.0)]
        )
        for q, key in [
            ("secretmonkey", "d"),
            ("stylemonkey", "d"),
            ("other", "d"),
            ("templateword", "e"),
            ("cafe\u0301", "a"),
            ("日本語", "a"),
        ]:
            self.assertEqual([r[0] for r in self.search(q)], [key])
        for q in ["description", "secretmon", "😀", None, "", "  ", "%"]:
            self.assertEqual(self.search(q), [])
        self.assertEqual([r[0] for r in self.search("%monkey%")], ["a", "c", "f"])

    def test_phrases_boundaries_and_repeated_terms(self):
        self.assertEqual([r[0] for r in self.search("monkey zoo")], ["a", "f"])
        self.assertEqual([r[0] for r in self.search('"monkey zoo"')], ["a"])
        self.assertEqual([r[0] for r in self.search('"monkey monkey"')], ["a"])
        self.assertEqual(self.search('"zoo monkey"'), [])
        self.assertEqual([r[0] for r in self.search('"A B C"')], ["e"])
        self.assertEqual(self.search('"A B"')[0][0], "e")

    def test_provenance_and_composed_join(self):
        sql = """SELECT s.content_id,m.snippet,n.text FROM search(?) s,
          unnest(s.matches) AS ms(m),unnest(m.node_indexes) AS ns(node_index)
          JOIN html_node n ON n.content_id=s.content_id AND n.node_index=ns.node_index
          ORDER BY s.content_id,n.node_index"""
        # The direct fixture has no default public schema, so qualify the view.
        rows = self.search(
            "monkey", sql.replace("JOIN html_node", "JOIN public_v1.html_node")
        )
        self.assertTrue(any(r[2] == "mon" for r in rows))
        self.assertTrue(any(r[2] == "key" for r in rows))
        self.assertTrue(all(len(r[1]) <= 240 for r in rows))

    def test_parameters_and_prepare_do_not_discover(self):
        from unittest.mock import patch

        with patch(
            "periplus.query.search.discover", side_effect=AssertionError("prep scans")
        ):
            b = bind_search(
                "SELECT * FROM search(?) WHERE score>?",
                ["monkey", 2],
                self.db,
                execute=False,
                check=lambda: None,
            )
            self.assertEqual(self.db.execute(b.sql, b.parameters).fetchall(), [])
        b = bind_search(
            "SELECT * FROM search($1) WHERE score>$2",
            ["monkey", 2],
            self.db,
            execute=True,
            check=lambda: None,
        )
        self.assertEqual(
            [r[0] for r in self.db.execute(b.sql, b.parameters).fetchall()], ["a"]
        )
        for q in ["x" * 257, '"unclosed', " ".join(["x"] * 33)]:
            with self.assertRaises(ValueError):
                self.search(q)
        with self.assertRaises(ValueError):
            bind_search(
                "SELECT * FROM search(content_id)",
                [],
                self.db,
                execute=True,
                check=lambda: None,
            )
        with self.assertRaisesRegex(duckdb.Error, "query API"):
            self.db.execute("SELECT * FROM public_v1.search('monkey')").fetchall()
        self.db.execute("DESCRIBE SELECT * FROM public_v1.search('monkey')").fetchall()

    def test_element_text_preserves_source_values(self):
        rows = dict(
            self.db.execute(
                "SELECT tag,text FROM public_v1.html_element WHERE content_id='d' AND tag IN ('p','script','style','title','div')"
            ).fetchall()
        )
        self.assertEqual(
            rows,
            {
                "p": "AB",
                "script": "secretmonkey",
                "style": "stylemonkey",
                "title": "Other",
                "div": "",
            },
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM public_v1.html_node WHERE node_type<>'text' AND text IS NOT NULL"
            ).fetchone(),
            (0,),
        )

    def test_phrase_filter_precedes_result_cap(self):
        for i in range(103):
            key = f"z{i:03}"
            matches = i >= 101
            self.db.execute("INSERT INTO public_v1.capture VALUES (?)", [key])
            self.db.execute(
                "INSERT INTO material.posting VALUES (?,?,1,[0],[[0]]),(?,?,1,?,[[0]])",
                ["monkey", key, "zoo", key, [1 if matches else 2]],
            )
            self.db.execute(
                "INSERT INTO material.html_nodes(content_sha256,node_index,node_type,value) VALUES (?,0,'text',?)",
                [key, "monkey zoo" if matches else "monkey something zoo"],
            )
        self.assertEqual(
            [r[0] for r in self.search('"monkey zoo"')], ["a", "z101", "z102"]
        )

    def test_snippet_preserves_case_and_unicode_offsets(self):
        from periplus.query.search import _snippet, parse_query

        text = "prefix " * 50 + "Straße cafe\u0301 end"
        snippet = _snippet(text, parse_query("STRASSE"))
        self.assertIn("Straße", snippet)
        self.assertIn("café", snippet)
        self.assertLessEqual(len(snippet), 240)

    def test_large_snippets_and_search_inspection(self):
        from periplus.query.search import _snippet, parse_query

        for prefix in ("padding " * 100000, "😀 café " * 10000):
            with self.subTest(unicode=not prefix.isascii()):
                snippet = _snippet(prefix + "Straße target", parse_query("STRASSE"))
                self.assertIn("Straße", snippet)
                self.assertLessEqual(len(snippet), 240)
        for prefix in ("EXPLAIN ", "EXPLAIN ANALYZE ", "SUMMARIZE "):
            with self.assertRaisesRegex(ValueError, "query preparation"):
                bind_search(
                    prefix + "SELECT * FROM search('monkey')",
                    [],
                    self.db,
                    execute=False,
                    check=lambda: None,
                )
        self.assertIsNone(
            bind_search(
                "DESCRIBE SELECT * FROM search('monkey')",
                [],
                self.db,
                execute=False,
                check=lambda: None,
            )
        )
