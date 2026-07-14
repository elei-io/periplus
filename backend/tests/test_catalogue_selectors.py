from __future__ import annotations

import time
import unittest

import duckdb

from dom import encode_html
from repository.catalogue.selectors import (
    SelectorCompileError,
    SelectorScopeRequired,
    compile_selector_predicate,
    compile_selector,
)


HTML = """
<!doctype html>
<html lang="en-US" dir="ltr">
  <head><title>Selectors</title></head>
  <body id="body">
    <main id="main" class="page shell" data-kind="landing-page" data-code="AbC">
      <article id="first" class="card featured" data-tags="one two" data-prefix="en-US">
        <h2 class="title">First</h2>
        <a id="details" class="button primary" href="/first" data-label="Read More">Read</a>
        <span id="after" class="after">After</span>
      </article>
      <article id="second" class="card" lang="fr">
        <h2 class="title">Second</h2>
        <a href="https://example.test/second">Second link</a>
      </article>
      <article id="third" class="card featured" dir="rtl">
        <h2 class="title">Third</h2>
        <a>Not a link</a>
      </article>
      <section id="empty"></section>
      <section id="whitespace"> </section>
      <div id="only-parent"><em id="only">Only</em></div>
      <div id="mixed-parent"><i id="i1"></i><b id="b1"></b><i id="i2"></i></div>
      <svg xmlns="http://www.w3.org/2000/svg">
        <circle id="dot"/>
        <linearGradient id="gradient" viewBox="0 0 1 1"></linearGradient>
      </svg>
    </main>
  </body>
</html>
"""


class SelectorCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = duckdb.connect(":memory:")
        cls.connection.execute(
            """
            CREATE TABLE elements (
                document_id VARCHAR NOT NULL,
                element_index INTEGER NOT NULL,
                parent_index INTEGER,
                subtree_end_index INTEGER NOT NULL,
                depth INTEGER NOT NULL,
                tag VARCHAR NOT NULL,
                namespace_uri VARCHAR,
                attributes MAP(VARCHAR, VARCHAR) NOT NULL,
                text_direct VARCHAR NOT NULL,
                text_tail VARCHAR NOT NULL
            )
            """
        )
        rows = encode_html(HTML)
        cls.connection.executemany(
            "INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "doc",
                    row.element_index,
                    row.parent_index,
                    row.subtree_end_index,
                    row.depth,
                    row.tag,
                    row.namespace_uri,
                    row.attributes,
                    row.text_direct,
                    row.text_tail,
                )
                for row in rows
            ],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def ids(self, selector: str, **parameters: object) -> list[str]:
        compiled = compile_selector(
            selector,
            namespaces={"svg": "http://www.w3.org/2000/svg"},
        )
        bindings = compiled.parameters | {"document_id": "doc"} | parameters
        return [
            value
            for (value,) in self.connection.execute(
                "SELECT map_extract_value(attributes, 'id') "
                f"FROM ({compiled.sql}) matched "
                "WHERE map_extract_value(attributes, 'id') IS NOT NULL "
                "ORDER BY element_index",
                bindings,
            ).fetchall()
        ]

    def test_composition_can_namespace_all_bindings(self) -> None:
        compiled = compile_selector(
            ":scope, :scope",
            parameter_prefix="atlas_test",
            document_parameter="atlas_document",
        )
        self.assertEqual(
            compiled.parameters,
            {"atlas_test_scope_element_index": 0},
        )
        self.assertEqual(compiled.required_parameters, ("atlas_document",))

    def test_element_universal_namespace_id_and_class_selectors(self) -> None:
        cases = {
            "*": [
                "body", "main", "first", "details", "after", "second", "third", "empty",
                "whitespace", "only-parent", "only", "mixed-parent", "i1", "b1", "i2",
                "dot", "gradient",
            ],
            "article": ["first", "second", "third"],
            "svg|circle": ["dot"],
            "svg|linearGradient": ["gradient"],
            "svg|lineargradient": [],
            "#details": ["details"],
            ".featured": ["first", "third"],
            ".page.shell": ["main"],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

    def test_foreign_element_attribute_names_remain_case_sensitive(self) -> None:
        self.assertEqual(self.ids("[viewBox]"), ["gradient"])
        self.assertEqual(self.ids("[viewbox]"), [])

    def test_every_attribute_operator_and_modifier(self) -> None:
        cases = {
            "[data-kind]": ["main"],
            '[data-kind="landing-page"]': ["main"],
            '[data-tags~="two"]': ["first"],
            '[data-prefix|="en"]': ["first"],
            '[data-kind^="landing"]': ["main"],
            '[data-kind$="page"]': ["main"],
            '[data-kind*="ding-pa"]': ["main"],
            '[data-code="abc" i]': ["main"],
            '[data-code="AbC" s]': ["main"],
            '[data-code="abc" s]': [],
            '[data-tags~="one two"]': [],
            '[data-kind^=""]': [],
            '[data-kind$=""]': [],
            '[data-kind*=""]': [],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

    def test_all_combinators_and_selector_lists(self) -> None:
        cases = {
            "main a": ["details"],
            "article > a": ["details"],
            "h2 + a": ["details"],
            "h2 ~ span": ["after"],
            "#details + span": ["after"],
            "article.featured > h2 + a[href]": ["details"],
            "#first, #third, #empty": ["first", "third", "empty"],
        }
        # Links without IDs are intentionally excluded by ids(); the first two cases still
        # prove descendant/child matching through the identified first link.
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

    def test_escaped_css_identifiers_and_values_are_decoded_then_bound(self) -> None:
        cases = {
            r"#det\61 ils": ["details"],
            r".prim\61 ry": ["details"],
            r'[data-label="Read\20 More"]': ["details"],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

    def test_logical_and_relational_pseudo_classes(self) -> None:
        cases = {
            "article:is(.featured, [lang])": ["first", "second", "third"],
            "article:where(.featured, [lang])": ["first", "second", "third"],
            "article:not(.featured)": ["second"],
            "article:not(.featured, [lang])": [],
            "article:has(> a[href])": ["first", "second"],
            "article:has(> h2 + a[href])": ["first", "second"],
            "main:has(article a[href])": ["main"],
            "article:has(+ article[lang])": ["first"],
            "article:has(~ article[dir=rtl])": ["first", "second"],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

        adjacent = compile_selector("article:has(+ article)")
        self.assertIn("css_previous_sibling_index", adjacent.sql)
        subsequent = compile_selector("article:has(~ article)")
        self.assertIn("SELECT max(", subsequent.sql)
        descendant = compile_selector("article:has(a[href])")
        self.assertIn("ROWS BETWEEN 1 FOLLOWING", descendant.sql)
        self.assertNotIn("SELECT count(*)", descendant.sql)

    def test_root_empty_and_child_position_pseudo_classes(self) -> None:
        cases = {
            ":root": [],
            ":empty": ["empty", "i1", "b1", "i2", "dot", "gradient"],
            "article:first-child": ["first"],
            "article:last-child": [],
            "article:only-child": [],
            "#only:only-child": ["only"],
            "article:nth-child(2)": ["second"],
            "article:nth-child(2n+1)": ["first", "third"],
            "article:nth-child(-n+2)": ["first", "second"],
            "article:nth-child(odd)": ["first", "third"],
            "article:nth-last-child(6)": ["third"],
            "article:nth-child(2 of .featured)": ["third"],
            "article:nth-last-child(1 of .featured)": ["third"],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

        filtered = compile_selector("article:nth-child(2 of .featured)")
        self.assertNotIn("SELECT count(*)", filtered.sql)
        self.assertIn("row_number() OVER", filtered.sql)

    def test_type_position_pseudo_classes(self) -> None:
        cases = {
            "article:first-of-type": ["first"],
            "article:last-of-type": ["third"],
            "article:only-of-type": [],
            "em:only-of-type": ["only"],
            "#i1:nth-of-type(1)": ["i1"],
            "#i2:nth-of-type(2)": ["i2"],
            "#i1:nth-last-of-type(2)": ["i1"],
            "#i2:nth-last-of-type(1)": ["i2"],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

    def test_language_direction_link_and_scope_pseudo_classes(self) -> None:
        cases = {
            "article:lang(en)": ["first", "third"],
            "article:lang(fr)": ["second"],
            "article:lang(en, fr)": ["first", "second", "third"],
            "article:dir(ltr)": ["first", "second"],
            "article:dir(rtl)": ["third"],
            "a:any-link": ["details"],
            ":scope": [],
        }
        for selector, expected in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(self.ids(selector), expected)

        self.assertEqual(self.ids(":scope", scope_element_index=4), ["main"])
        self.assertEqual(
            self.ids(":scope:is(:scope)", scope_element_index=4),
            ["main"],
        )

    def test_namespace_prefix_must_be_declared(self) -> None:
        with self.assertRaisesRegex(SelectorCompileError, "namespace"):
            compile_selector("missing|circle")

        compiled = compile_selector(
            "circle",
            namespaces={None: "http://www.w3.org/2000/svg"},
        )
        matches = self.connection.execute(
            "SELECT map_extract_value(attributes, 'id') "
            f"FROM ({compiled.sql})",
            compiled.parameters | {"document_id": "doc"},
        ).fetchall()
        self.assertEqual(matches, [("dot",)])

    def test_values_are_bound_parameters_not_sql_text(self) -> None:
        compiled = compile_selector('[data-label="Read More"]')
        self.assertNotIn("Read More", compiled.sql)
        self.assertIn("Read More", compiled.parameters.values())
        self.assertEqual(self.ids('[data-label="Read More"]'), ["details"])

    def test_row_local_predicate_compiles_without_a_document_binding(self) -> None:
        compiled = compile_selector_predicate(
            ":is(article.card, [href]):not(#second)",
            subject="e",
            parameter_prefix="local",
        )
        matches = self.connection.execute(
            "SELECT map_extract_value(e.attributes, 'id') FROM elements e "
            f"WHERE {compiled.sql} "
            "AND map_extract_value(e.attributes, 'id') IS NOT NULL "
            "ORDER BY e.element_index",
            compiled.parameters,
        ).fetchall()
        self.assertEqual(matches, [("first",), ("details",), ("third",)])

    def test_structural_predicate_requires_a_document_scope(self) -> None:
        for selector in (
            "article > a",
            "article:has(a)",
            ":empty",
            ":first-child",
            ":nth-child(2n+1)",
            ":lang(en)",
            ":dir(rtl)",
        ):
            with self.subTest(selector=selector), self.assertRaises(
                SelectorScopeRequired
            ):
                compile_selector_predicate(selector, subject="e")

    def test_invalid_unknown_and_pseudo_element_selectors_fail_clearly(self) -> None:
        for selector in (
            "",
            "article >",
            ":hover",
            ":made-up(value)",
            "article::before",
            ":nth-child(nope)",
            ":nth-of-type(2 of .card)",
            ":lang()",
            ":lang(en fr)",
            ":dir(auto)",
            "[*|href]",
        ):
            with self.subTest(selector=selector):
                with self.assertRaises(SelectorCompileError):
                    compile_selector(selector)

    def test_compiled_query_is_bounded_to_one_document_when_bound(self) -> None:
        compiled = compile_selector("article.featured")
        count = self.connection.execute(
            f"SELECT count(*) FROM ({compiled.sql})",
            compiled.parameters | {"document_id": "missing"},
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_document_scope_is_required_and_precedes_window_work(self) -> None:
        compiled = compile_selector("article:nth-child(odd)")
        self.assertEqual(compiled.required_parameters, ("document_id",))
        self.assertLess(
            compiled.sql.index("WHERE document_id = $document_id"),
            compiled.sql.index("row_number() OVER"),
        )
        with self.assertRaises(duckdb.Error):
            self.connection.execute(compiled.sql, compiled.parameters)

    def test_document_binding_does_not_leak_matches_between_documents(self) -> None:
        rows = self.connection.execute(
            "SELECT * REPLACE ('other' AS document_id) FROM elements"
        ).fetchall()
        self.connection.executemany(
            "INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
        try:
            compiled = compile_selector("article.featured")
            counts = {
                document_id: self.connection.execute(
                    f"SELECT count(*) FROM ({compiled.sql})",
                    compiled.parameters | {"document_id": document_id},
                ).fetchone()[0]
                for document_id in ("doc", "other")
            }
            self.assertEqual(counts, {"doc": 2, "other": 2})
        finally:
            self.connection.execute("DELETE FROM elements WHERE document_id = 'other'")

    def test_large_document_selector_stays_interactive(self) -> None:
        compiled = compile_selector("div.item:nth-child(2n+1):has(> a[href])")
        self.assertIn("row_number() OVER", compiled.sql)
        self.assertNotIn("SELECT count(*)", compiled.sql)
        start = time.perf_counter()
        for _ in range(25):
            self.connection.execute(
                compiled.sql,
                compiled.parameters | {"document_id": "doc"},
            ).fetchall()
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
