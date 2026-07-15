from __future__ import annotations

import unittest

import duckdb

from repository.catalogue.selector_sql import (
    SelectorSqlRewriteError,
    rewrite_css_select,
)


HTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
ELEMENTS = [
    (0, None, 7, 0, "html", {}),
    (1, 0, 7, 1, "body", {}),
    (2, 1, 7, 2, "main", {"id": "main"}),
    (3, 2, 4, 3, "article", {"id": "first", "class": "card featured"}),
    (4, 3, 4, 4, "a", {"id": "one-link", "href": "/one"}),
    (5, 2, 6, 3, "article", {"id": "second", "class": "card"}),
    (6, 5, 6, 4, "a", {"id": "two-link"}),
    (7, 2, 7, 3, "aside", {"id": "other", "class": "featured"}),
]


class SelectorSqlRewriteTests(unittest.TestCase):
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
            );
            CREATE TABLE crawls (
                crawl_id VARCHAR PRIMARY KEY,
                document_id VARCHAR NOT NULL
            )
            """
        )
        cls.connection.executemany(
            "INSERT INTO elements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "doc",
                    element_index,
                    parent_index,
                    subtree_end_index,
                    depth,
                    tag,
                    HTML_NAMESPACE,
                    attributes,
                    "",
                    "",
                )
                for (
                    element_index,
                    parent_index,
                    subtree_end_index,
                    depth,
                    tag,
                    attributes,
                ) in ELEMENTS
            ],
        )
        cls.connection.execute("INSERT INTO crawls VALUES ('crawl', 'doc')")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def execute_ids(
        self, sql: str, parameters: dict[str, object] | None = None
    ) -> list[str]:
        rewritten = rewrite_css_select(sql)
        bindings = rewritten.parameters | (parameters or {})
        return [
            value
            for (value,) in self.connection.execute(rewritten.sql, bindings).fetchall()
        ]

    def test_row_local_selector_is_inlined_without_a_document_scope(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT map_extract_value(attributes, 'id')
            FROM elements
            WHERE css_select('a')
            ORDER BY element_index
            LIMIT 100
            """
        )
        self.assertNotIn("CSS_SELECT", rewritten.sql.upper())
        self.assertNotIn("AS MATERIALIZED", rewritten.sql.upper())
        self.assertNotIn("EXISTS", rewritten.sql.upper())
        self.assertEqual(rewritten.required_parameters, ())
        self.assertEqual(
            self.execute_ids(
                """
                SELECT map_extract_value(attributes, 'id')
                FROM elements
                WHERE css_select('a')
                ORDER BY element_index
                LIMIT 100
                """
            ),
            ["one-link", "two-link"],
        )

    def test_structural_selector_uses_a_bounded_materialized_semijoin(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT e.element_index FROM elements e
            WHERE e.document_id = $document_id
              AND css_select('article > a[href]')
            """
        )
        self.assertIn("AS MATERIALIZED", rewritten.sql.upper())
        self.assertIn("EXISTS", rewritten.sql.upper())
        self.assertEqual(rewritten.required_parameters, ("document_id",))

    def test_unbounded_structural_selector_runs_globally(self) -> None:
        sql = """
            SELECT map_extract_value(attributes, 'id')
            FROM elements
            WHERE css_select('article > a')
            ORDER BY element_index
            LIMIT 100
        """
        rewritten = rewrite_css_select(sql)
        self.assertIn("AS MATERIALIZED", rewritten.sql.upper())
        self.assertEqual(rewritten.required_parameters, ())
        self.assertEqual(
            rewritten.unbounded_structural_selectors,
            ("article > a",),
        )
        self.assertEqual(self.execute_ids(sql), ["one-link", "two-link"])

    def test_unprovable_bounds_fall_back_to_global_execution(self) -> None:
        queries = (
            "SELECT * FROM elements e WHERE "
            "(e.document_id=$document_id OR e.tag='article') "
            "AND css_select(e, 'article:first-child')",
            "SELECT * FROM elements e WHERE e.document_id=$one "
            "AND e.document_id=$two AND css_select(e, 'article:first-child')",
            "SELECT * FROM elements e WHERE e.document_id=lower($document_id) "
            "AND css_select(e, 'article:first-child')",
        )
        for sql in queries:
            with self.subTest(sql=sql):
                rewritten = rewrite_css_select(sql)
                self.assertEqual(
                    rewritten.unbounded_structural_selectors,
                    ("article:first-child",),
                )

    def test_one_argument_form_infers_one_aliased_elements_source(self) -> None:
        self.assertEqual(
            self.execute_ids(
                """
                SELECT map_extract_value(node.attributes, 'id')
                FROM elements AS node
                WHERE node.document_id = $document_id
                  AND css_select('.card')
                ORDER BY node.element_index
                """,
                {"document_id": "doc"},
            ),
            ["first", "second"],
        )

    def test_one_argument_form_supports_an_unaliased_elements_source(self) -> None:
        self.assertEqual(
            self.execute_ids(
                """
                SELECT map_extract_value(attributes, 'id')
                FROM elements
                WHERE document_id = $document_id
                  AND css_select('.featured')
                ORDER BY element_index
                """,
                {"document_id": "doc"},
            ),
            ["first", "other"],
        )

    def test_row_local_selector_lists_and_logical_pseudos_need_no_scope(self) -> None:
        self.assertEqual(
            self.execute_ids(
                """
                SELECT map_extract_value(attributes, 'id')
                FROM elements
                WHERE css_select(':is(article.card, aside):not(#second)')
                ORDER BY element_index
                """
            ),
            ["first", "other"],
        )

    def test_css_select_is_a_boolean_expression_in_the_projection(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT map_extract_value(attributes, 'id') AS id,
                   css_select('.featured') AS featured
            FROM elements
            WHERE map_extract_value(attributes, 'id') IS NOT NULL
            ORDER BY element_index
            """
        )
        rows = self.connection.execute(
            rewritten.sql,
            rewritten.parameters,
        ).fetchall()
        self.assertEqual(
            rows,
            [
                ("main", False),
                ("first", True),
                ("one-link", False),
                ("second", False),
                ("two-link", False),
                ("other", True),
            ],
        )

    def test_structural_selector_is_equivalent_inside_nested_boolean_logic(self) -> None:
        ids = self.execute_ids(
            """
            SELECT map_extract_value(e.attributes, 'id')
            FROM elements e
            WHERE e.document_id = $doc
              AND (css_select(e, 'article:has(> a[href])') OR
                   css_select(e, 'aside.featured'))
              AND NOT css_select(e, '#second')
            ORDER BY e.element_index
            LIMIT 10
            """,
            {"doc": "doc"},
        )
        self.assertEqual(ids, ["first", "other"])

    def test_identical_calls_are_deduplicated(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT e.element_index FROM elements e
            WHERE e.document_id = $document_id
              AND (css_select(e, 'article:has(a)') OR css_select(e, 'article:has(a)'))
            """
        )
        self.assertEqual(rewritten.sql.upper().count("AS MATERIALIZED"), 1)

    def test_different_calls_have_disjoint_generated_bindings(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT e.element_index FROM elements e
            WHERE e.document_id = $document_id
              AND css_select(e, 'article:first-child')
              AND NOT css_select(e, 'article:last-child')
            """
        )
        self.assertEqual(rewritten.sql.upper().count("AS MATERIALIZED"), 2)
        self.assertEqual(len(rewritten.parameters), len(set(rewritten.parameters)))
        self.assertTrue(all(name.startswith("atlas_css_") for name in rewritten.parameters))

    def test_same_selector_can_target_both_sides_of_a_self_join(self) -> None:
        rows = self.execute_ids(
            """
            SELECT map_extract_value(left_e.attributes, 'id')
            FROM elements left_e
            JOIN elements right_e
              ON right_e.document_id = left_e.document_id
             AND right_e.element_index = left_e.element_index
            WHERE left_e.document_id = $document_id
              AND right_e.document_id = $document_id
              AND css_select(left_e, '.featured')
              AND css_select(right_e, '.featured')
            ORDER BY left_e.element_index
            """,
            {"document_id": "doc"},
        )
        self.assertEqual(rows, ["first", "other"])

    def test_preserves_an_existing_with_clause(self) -> None:
        rewritten = rewrite_css_select(
            """
            WITH bounded AS MATERIALIZED (
              SELECT * FROM elements WHERE document_id = $document_id
            )
            SELECT e.element_index
            FROM elements e
            WHERE e.document_id = $document_id AND css_select(e, 'article:first-child')
            """
        )
        self.assertIn("BOUNDED AS MATERIALIZED", rewritten.sql.upper())
        self.assertEqual(rewritten.sql.upper().count("AS MATERIALIZED"), 2)

    def test_literal_document_scope_is_parameterized(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT map_extract_value(e.attributes, 'id')
            FROM elements e
            WHERE e.document_id = 'doc' AND css_select(e, '#first:first-child')
            """
        )
        self.assertNotIn("'doc'", rewritten.sql)
        self.assertEqual(rewritten.required_parameters, ())
        self.assertEqual(
            [value for (value,) in self.connection.execute(
                rewritten.sql, rewritten.parameters
            ).fetchall()],
            ["first"],
        )

    def test_crawl_scope_is_resolved_before_selector_windows(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT map_extract_value(e.attributes, 'id')
            FROM crawls c JOIN elements e USING (document_id)
            WHERE c.crawl_id = $crawl_id AND css_select(e, 'article:nth-child(1)')
            ORDER BY e.element_index
            """
        )
        self.assertEqual(rewritten.required_parameters, ("crawl_id",))
        self.assertLess(
            rewritten.sql.upper().find("WHERE DOCUMENT_ID ="),
            rewritten.sql.upper().find("ROW_NUMBER() OVER"),
        )
        rows = self.connection.execute(
            rewritten.sql, rewritten.parameters | {"crawl_id": "crawl"}
        ).fetchall()
        self.assertEqual(rows, [("first",)])

    def test_user_parameter_names_cannot_collide_with_generated_bindings(self) -> None:
        rewritten = rewrite_css_select(
            """
            SELECT e.element_index FROM elements e
            WHERE e.document_id = $atlas_css_0_tag_0
              AND css_select(e, 'article:nth-child(1)')
            """
        )
        self.assertIn("atlas_css_0_tag_0", rewritten.required_parameters)
        self.assertNotIn("atlas_css_0_tag_0", rewritten.parameters)

    def test_no_selector_is_a_valid_normalizing_noop(self) -> None:
        rewritten = rewrite_css_select(
            "SELECT * FROM elements WHERE document_id = $document_id"
        )
        self.assertEqual(rewritten.parameters, {})
        self.assertEqual(rewritten.required_parameters, ("document_id",))

    def test_crawl_scope_accepts_an_explicit_document_join(self) -> None:
        ids = self.execute_ids(
            """
            SELECT map_extract_value(e.attributes, 'id')
            FROM crawls c
            JOIN elements e ON e.document_id = c.document_id
            WHERE $crawl_id = c.crawl_id AND css_select(e, 'article:has(a)')
            ORDER BY e.element_index
            """,
            {"crawl_id": "crawl"},
        )
        self.assertEqual(ids, ["first", "second"])

    def test_generated_relation_and_scope_names_avoid_query_names(self) -> None:
        rewritten = rewrite_css_select(
            """
            WITH atlas_css_match_0 AS (SELECT 1)
            SELECT e.element_index
            FROM elements e CROSS JOIN (SELECT 1) atlas_css_rows_0
            WHERE e.document_id = 'doc'
              AND $atlas_css_scope_0 IS NOT NULL
              AND css_select(e, 'article:nth-child(1)')
            """
        )
        self.assertIn("atlas_css_match_0_1", rewritten.sql)
        self.assertIn("atlas_css_scope_0_1", rewritten.parameters)

    def test_rejects_unsafe_or_ambiguous_forms(self) -> None:
        cases = {
            "dynamic selector": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select(e, e.tag)"
            ),
            "qualified first argument": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select(e.element_index, '.card')"
            ),
            "unknown alias": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select(x, '.card')"
            ),
            "wrong table": (
                "SELECT * FROM crawls c WHERE c.document_id=$document_id "
                "AND css_select(c, '.card')"
            ),
            "wrong arity": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select()"
            ),
            "too many arguments": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select(e, '.card', 'extra')"
            ),
            "inference without elements": (
                "SELECT * FROM crawls c WHERE c.crawl_id=$crawl_id "
                "AND css_select('.card')"
            ),
            "ambiguous inference": (
                "SELECT * FROM elements left_e CROSS JOIN elements right_e "
                "WHERE left_e.document_id=$document_id "
                "AND right_e.document_id=$document_id AND css_select('.card')"
            ),
            "multiple statements": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select(e, '.card'); SELECT 1"
            ),
            "set operation": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND css_select(e, '.card') UNION ALL SELECT * FROM elements"
            ),
            "nested select": (
                "SELECT * FROM elements e WHERE e.document_id=$document_id "
                "AND EXISTS (SELECT 1 WHERE css_select(e, '.card'))"
            ),
            "implicit table name": (
                "SELECT * FROM elements WHERE elements.document_id=$document_id "
                "AND css_select(elements, '.card')"
            ),
            "duplicate alias": (
                "SELECT * FROM elements e CROSS JOIN elements e "
                "WHERE e.document_id=$document_id AND css_select(e, '.card')"
            ),
        }
        for label, sql in cases.items():
            with self.subTest(label=label), self.assertRaises(SelectorSqlRewriteError):
                rewrite_css_select(sql)

    def test_rejects_invalid_or_runtime_only_css(self) -> None:
        for selector in ("[", ":hover"):
            with self.subTest(selector=selector), self.assertRaises(
                SelectorSqlRewriteError
            ):
                rewrite_css_select(
                    "SELECT * FROM elements e WHERE e.document_id=$document_id "
                    f"AND css_select(e, '{selector}')"
                )


if __name__ == "__main__":
    unittest.main()
