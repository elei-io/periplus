import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import duckdb

from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.search_text import build_search_text

spec = importlib.util.spec_from_file_location(
    "search_matches_experiment",
    Path(__file__).resolve().parents[3]
    / "benchmarks/query/experiments/search_matches.py",
)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class SearchMatchesExperimentTests(unittest.TestCase):
    def setUp(self):
        self.db = duckdb.connect()
        self.addCleanup(self.db.close)

    def matches(self, source, query):
        nodes, _ = parse_document(source)
        matches = experiment.matched_snippets(
            self.db, nodes, query, expected_prose=build_search_text(nodes).prose
        )
        return nodes, matches

    def test_split_word_points_to_both_text_nodes_not_context(self):
        nodes, matches = self.matches(
            "<p>Context <i>mon</i><b>key</b> after</p>", "monkey"
        )
        indexes = [n.node_index for n in nodes if n.value in ("mon", "key")]
        self.assertEqual(
            matches, [{"snippet": "Context monkey after", "node_indexes": indexes}]
        )

    def test_phrase_across_blocks_and_normalized_whitespace(self):
        nodes, matches = self.matches(
            "<p>monkeys</p><p> in\n the <b>zoo</b></p>", "  MONKEYS in\t the zoo "
        )
        self.assertEqual(matches[0]["snippet"], "monkeys in the zoo")
        self.assertEqual(
            matches[0]["node_indexes"],
            [n.node_index for n in nodes if n.node_type == "text"],
        )

    def test_composed_unicode_across_nodes(self):
        nodes, matches = self.matches("<p>cafe<b>\u0301</b> 日本語 😀</p>", "CAFÉ")
        self.assertEqual(
            matches[0]["node_indexes"],
            [n.node_index for n in nodes if n.value in ("cafe", "\u0301")],
        )
        self.assertIn("café", matches[0]["snippet"])
        _, matches = self.matches("<p>日本語 😀 İ Σ ẞ</p>", "😀")
        self.assertEqual(len(matches), 1)

    def test_empty_body(self):
        self.assertEqual(self.matches("<p></p>", "monkey")[1], [])

    def test_body_scope_and_literal_punctuation(self):
        source = "<title>monkey</title><script>monkey</script><style>monkey</style><template>monkey</template><p>100% a_b</p>"
        for query in ("monkey", None, "", "  \n"):
            self.assertEqual(self.matches(source, query)[1], [])
        for query in ("%", "_"):
            self.assertEqual(len(self.matches(source, query)[1]), 1)

    def test_bounded_distinct_matches_and_snippets(self):
        source = (
            "<body>"
            + "".join("<p>monkey</p><p>" + "x" * 300 + "</p>" for _ in range(5))
            + "</body>"
        )
        nodes, matches = self.matches(source, "monkey")
        expected = [n.node_index for n in nodes if n.value == "monkey"][:3]
        self.assertEqual([m["node_indexes"] for m in matches], [[i] for i in expected])
        self.assertTrue(all(len(m["snippet"]) <= 240 for m in matches))

    def test_fail_closed_on_prose_mismatch_and_bounds(self):
        nodes, _ = parse_document("<p>monkey</p>")
        with self.assertRaisesRegex(ValueError, "differs"):
            experiment.matched_snippets(
                self.db, nodes, "monkey", expected_prose="other"
            )
        with (
            patch.object(experiment, "MAX_BODY_CHARS", 3),
            self.assertRaisesRegex(ValueError, "budget"),
        ):
            experiment.matched_snippets(self.db, nodes, "monkey")
        with self.assertRaisesRegex(ValueError, "256"):
            experiment.matched_snippets(self.db, nodes, "a" * 257)

    def test_two_stage_result_shape_and_capture_deduplication(self):
        from test_search_contract import SearchContractTests

        fixture = SearchContractTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        rows, metrics = experiment.prototype_search(fixture.db, "monkey")
        self.assertEqual([r["content_id"] for r in rows], ["a", "c"])
        self.assertEqual(set(rows[0]), {"content_id", "matches", "score"})
        self.assertEqual(set(rows[0]["matches"][0]), {"snippet", "node_indexes"})
        self.assertEqual(len(rows[0]["matches"][0]["node_indexes"]), 2)
        self.assertEqual(metrics["contents"], 2)
        import pyarrow as pa

        fixture.db.register("search_trial", pa.Table.from_pylist(rows))
        joined = fixture.db.execute("""SELECT n.text,e.tag
            FROM search_trial s
            CROSS JOIN unnest(s.matches) AS m(hit)
            CROSS JOIN unnest(m.hit.node_indexes) AS i(node_index)
            JOIN public_v1.html_node n ON n.content_id=s.content_id AND n.node_index=i.node_index
            JOIN public_v1.html_element e ON e.content_id=n.content_id AND e.node_index=n.parent_index
            WHERE s.content_id='a' ORDER BY n.node_index""").fetchall()
        self.assertEqual(joined, [("mon", "p"), ("key", "strong")])
