import unittest
from importlib.resources import files
import duckdb
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME


class SearchContractTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)
        self.db.execute("CREATE SCHEMA material; CREATE SCHEMA public_v1")
        pages = {
            "a": '<title>MONKEY zoo</title><meta name="description" content="monkey description"><p>mon<strong>key</strong> café 日本語 100% a_b</p>',
            "b": '<meta name="description" content="monkey description"><p>Something else</p>',
            "c": "<p>monkey body</p>",
            "e": '<title>Whitespace</title><meta name=description content="zebra zzz"><meta name=description content="zebra aaa"><p> A\n <b>B\tC</b><!--omit--> D&nbsp;E </p>',
            "d": "<title>Other</title><script>secretmonkey</script><style>stylemonkey</style><p>A<br>B</p><div></div>",
        }
        parsed = {k: parse_document(v) for k, v in pages.items()}
        context = VisitBatchContext(
            (),
            (),
            (),
            {k: v[1] for k, v in parsed.items()},
            {k: v[0] for k, v in parsed.items()},
            {},
            frozenset(parsed),
        )
        for name in ("html_nodes", "prose"):
            self.db.register("rows", BY_NAME[name].rows(context))
            self.db.execute(f"CREATE TABLE material.{name} AS SELECT * FROM rows")
        self.db.execute(
            "CREATE TABLE public_v1.capture(content_id VARCHAR, effective_url VARCHAR, requested_url VARCHAR, captured_at TIMESTAMP, capture_id UUID)"
        )
        for i, k in enumerate(pages, 1):
            self.db.execute(
                "INSERT INTO public_v1.capture VALUES (?, ?, ?, '2026-01-01', ?::UUID)",
                [
                    k,
                    f"https://{k}",
                    f"https://{k}",
                    f"00000000-0000-0000-0000-{i:012d}",
                ],
            )
        self.db.execute(
            "INSERT INTO public_v1.capture VALUES ('a',NULL,'https://new-a','2026-02-01','00000000-0000-0000-0000-000000000010')"
        )
        root = files("periplus.platform.catalogue").joinpath("sql/public_v1")
        for resource in (
            "views/html_node.sql",
            "views/html_element.sql",
            "helpers/search.sql",
        ):
            self.db.execute(root.joinpath(resource).read_text())

    def test_search_grain_ranking_and_literals(self):
        rows = self.db.execute(
            "SELECT * FROM public_v1.search('  MoNkEy  ')"
        ).fetchall()
        self.assertEqual(
            [(r[0], r[4]) for r in rows], [("a", 6.0), ("b", 3.0), ("c", 1.0)]
        )
        self.assertEqual(rows[0][2], "https://new-a")
        self.assertIn("MONKEY", rows[0][3])
        for q in ("%", "_", "cafe\u0301", "日本語", "monkey café"):
            self.assertEqual(
                self.db.execute(
                    "SELECT content_id FROM public_v1.search(?)", [q]
                ).fetchall(),
                [("a",)],
            )
        for q in (
            None,
            "",
            " \t\n ",
            "secretmonkey",
            "stylemonkey",
            "%monkey%",
            "' OR true --",
        ):
            self.assertEqual(
                self.db.execute("SELECT * FROM public_v1.search(?)", [q]).fetchall(), []
            )
        with self.assertRaisesRegex(duckdb.Error, "at most 256"):
            self.db.execute("SELECT * FROM public_v1.search(?)", ["a" * 257]).fetchall()

    def test_element_text_complete_and_exact(self):
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
        p = self.db.execute(
            "SELECT text,text_direct FROM public_v1.html_element WHERE content_id='a' AND tag='p'"
        ).fetchone()
        self.assertEqual(p, ("monkey café 日本語 100% a_b", "mon café 日本語 100% a_b"))
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM public_v1.html_node WHERE node_type<>'text' AND text IS NOT NULL"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT count(*) FROM public_v1.html_node WHERE node_type='text' AND text IS DISTINCT FROM value"
            ).fetchone(),
            (0,),
        )

    def test_whitespace_and_source_order(self):
        self.assertEqual(
            self.db.execute(
                "SELECT text,text_direct FROM public_v1.html_element WHERE content_id='e' AND tag='p'"
            ).fetchone(),
            (" A\n B\tC D\u00a0E ", " A\n  D\u00a0E "),
        )
        self.assertEqual(
            self.db.execute("SELECT snippet FROM public_v1.search('zebra')").fetchone(),
            ("zebra zzz",),
        )
        self.assertEqual(
            self.db.execute(
                "SELECT content_id FROM public_v1.search('A  B\tC')"
            ).fetchall(),
            [("e",)],
        )
        self.assertEqual(
            self.db.execute(
                "SELECT text FROM public_v1.html_node WHERE node_type='comment'"
            ).fetchall(),
            [(None,)],
        )

    def test_bound_and_deterministic_ties(self):
        self.db.execute(
            "INSERT INTO material.prose SELECT 'z'||lpad(i::VARCHAR,3,'0'),'monkey' FROM range(150) t(i)"
        )
        self.db.execute(
            "INSERT INTO public_v1.capture SELECT 'z'||lpad(i::VARCHAR,3,'0'),'url','url',NULL,uuid() FROM range(150) t(i)"
        )
        rows = self.db.execute(
            "SELECT content_id FROM public_v1.search('monkey')"
        ).fetchall()
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[:3], [("a",), ("b",), ("c",)])
        self.assertEqual(rows[3:], [(f"z{i:03}",) for i in range(97)])
