"""Complete positional indexing and structural phrase boundaries."""

import unittest
from collections import Counter
from importlib.resources import files
from unittest.mock import patch

import duckdb

from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.materialization.search_text import build_search_text, text_runs
from periplus.materialization.tokenization import term_counts


class NodeLayoutTests(unittest.TestCase):
    def index(self, html):
        nodes, _ = parse_document(html)
        return nodes, build_search_text(nodes)

    def test_all_text_locations(self):
        nodes, result = self.index(
            '<title>titleword</title><meta content="attributeonly"><body hidden>bodyword<script>scriptword</script><style>styleword</style><template>templateword</template><noscript>noscriptword</noscript><!--commentonly--><svg><text>svgword</text></svg></body>'
        )
        self.assertEqual(
            set(result.occurrences),
            {
                "titleword",
                "bodyword",
                "scriptword",
                "styleword",
                "templateword",
                "noscriptword",
                "svgword",
            },
        )
        for items in result.occurrences.values():
            for item in items:
                self.assertTrue(item.node_indexes)
                self.assertTrue(
                    all(nodes[i].node_type == "text" for i in item.node_indexes)
                )

    def test_unicode_inline_provenance(self):
        for source in [
            "<p>mon<strong>key</strong> monkey</p>",
            "<p>caf<span>e</span>\u0301</p>",
            "<p>😀 StraßE 日本語 中文 한글</p>",
            "<p>ᄀ<span>ᅡ</span> ẞ İ ﬃ</p>",
        ]:
            nodes, result = self.index(source)
            expected = Counter()
            for run in text_runs(nodes):
                expected.update(term_counts("".join(n.value or "" for n in run)))
            self.assertEqual(
                {t: len(xs) for t, xs in result.occurrences.items()}, expected
            )
        _, result = self.index("<p>mon<strong>key</strong> monkey</p>")
        self.assertEqual(
            [len(x.node_indexes) for x in result.occurrences["monkey"]], [2, 1]
        )
        _, result = self.index("<p>caf<span>e</span>\u0301</p>")
        self.assertEqual(len(result.occurrences["café"][0].node_indexes), 3)

    def test_phrase_boundaries(self):
        _, index = self.index(
            '<title>one</title><body><p>two <b>three</b></p><p>four<br>five</p><x-custom>six</x-custom><p style="display:inline">seven</p></body>'
        )
        p = {t: xs[0].position for t, xs in index.occurrences.items()}
        self.assertEqual(p["three"], p["two"] + 1)
        for a, b in [
            ("one", "two"),
            ("three", "four"),
            ("four", "five"),
            ("five", "six"),
            ("six", "seven"),
        ]:
            self.assertGreater(p[b], p[a] + 1)
        self.assertEqual(
            set(self.index("<p>mon<!--ignored--><wbr>key</p>")[1].occurrences),
            {"monkey"},
        )

    def test_empty_nonword(self):
        for html in ["", "<p></p>", "<p> \n ! 😀 </p>"]:
            self.assertEqual(self.index(html)[1].occurrences, {})

    def test_element_projection_and_shared_index(self):
        nodes, elements = parse_document("<p>mon<strong>key</strong> monkey</p>")
        context = VisitBatchContext(
            (), (), (), {"a": elements}, {"a": nodes}, {}, frozenset({"a"})
        )
        with patch(
            "periplus.materialization.document_projection.build_search_text",
            wraps=build_search_text,
        ) as build:
            terms = BY_NAME["term"].rows(context).to_pylist()
            context.dictionary_ids["term"] = {
                r["text"]: i for i, r in enumerate(terms, 1)
            }
            rows = BY_NAME["posting"].rows(context).to_pylist()
            BY_NAME["posting"].rows(context)
            self.assertEqual(build.call_count, 1)
        self.assertFalse(
            {"prose", "content_posting", "node_posting", "html_elements"}
            & BY_NAME.keys()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["frequency"], 2)
        self.assertEqual(rows[0]["positions"][1], rows[0]["positions"][0] + 1)
        self.assertEqual([len(x) for x in rows[0]["node_indexes"]], [2, 1])
        with duckdb.connect() as db:
            db.execute("CREATE SCHEMA material; CREATE SCHEMA public_v1")
            db.register("rows", BY_NAME["html_nodes"].rows(context))
            db.execute("CREATE TABLE material.html_nodes AS SELECT * FROM rows")
            db.execute(
                files("periplus.platform.catalogue")
                .joinpath("sql/public_v1/views/html_element.sql")
                .read_text()
            )
            self.assertEqual(
                db.execute(
                    "SELECT node_index,tag FROM public_v1.html_element ORDER BY node_index"
                ).fetchall(),
                [(e.element_index, e.tag.lower()) for e in elements],
            )

    def test_ascii_fast_path_matches_unicode_provenance_path(self):
        from dataclasses import replace

        from periplus.materialization.search_text import _tokens

        class GenericText(str):
            def isascii(self):
                return False

        nodes, _ = self.index(
            "<p>mon<strong>key</strong> robot <i>12</i>34 empty<span></span>end</p>"
        )
        for run in text_runs(nodes):
            generic = tuple(replace(n, value=GenericText(n.value or "")) for n in run)
            self.assertEqual(list(_tokens(run)), list(_tokens(generic)))
