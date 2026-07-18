from __future__ import annotations

import unittest

import duckdb

from repository.catalogue.helper_sql import (
    HelperSqlRewriteError,
    rewrite_dom_helpers,
)


class DomHelperSqlRewriteTests(unittest.TestCase):
    def test_infers_the_only_elements_source_for_short_helpers(self) -> None:
        rewritten = rewrite_dom_helpers(
            """
            SELECT get_attribute('href'), has_attribute('href'),
                   readable_text(), text_content(), inner_html()
            FROM elements
            """
        )
        normalized = rewritten.upper()
        self.assertIn("GET_ATTRIBUTE(ELEMENTS.ATTRIBUTES, 'HREF')", normalized)
        self.assertIn("HAS_ATTRIBUTE(ELEMENTS.ATTRIBUTES, 'HREF')", normalized)
        for helper in ("READABLE_TEXT", "TEXT_CONTENT", "INNER_HTML"):
            self.assertIn(
                f"{helper}(ELEMENTS.DOCUMENT_ID, ELEMENTS.ELEMENT_INDEX)",
                normalized,
            )

    def test_explicit_alias_form_handles_multiple_element_sources(self) -> None:
        rewritten = rewrite_dom_helpers(
            """
            SELECT get_attribute(child, 'href'), readable_text(parent)
            FROM elements parent CROSS JOIN elements child
            """
        )
        normalized = rewritten.upper()
        self.assertIn("GET_ATTRIBUTE(CHILD.ATTRIBUTES, 'HREF')", normalized)
        self.assertIn(
            "READABLE_TEXT(PARENT.DOCUMENT_ID, PARENT.ELEMENT_INDEX)",
            normalized,
        )

    def test_existing_storage_level_signatures_remain_valid(self) -> None:
        sql = (
            "SELECT get_attribute(e.attributes, 'href'), "
            "readable_text(e.document_id, e.element_index) FROM elements e"
        )
        rewritten = rewrite_dom_helpers(sql)
        self.assertIn("macros.get_attribute(e.attributes, 'href')", rewritten)
        self.assertIn(
            "macros.readable_text(e.document_id, e.element_index)", rewritten
        )

    def test_rewritten_helpers_execute_as_normal_duckdb_macros(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """
                CREATE TABLE elements (
                    document_id VARCHAR,
                    element_index INTEGER,
                    attributes MAP(VARCHAR, VARCHAR)
                );
                INSERT INTO elements VALUES ('doc', 7, map(['href'], ['/target']));
                CREATE SCHEMA macros;
                CREATE MACRO macros.get_attribute(attrs, name)
                    AS map_extract_value(attrs, name);
                CREATE MACRO macros.readable_text(document_id, element_index)
                    AS document_id || ':' || element_index::VARCHAR;
                """
            )
            rewritten = rewrite_dom_helpers(
                "SELECT get_attribute('href'), readable_text() FROM elements"
            )
            self.assertEqual(
                connection.execute(rewritten).fetchall(),
                [("/target", "doc:7")],
            )
        finally:
            connection.close()

    def test_ambiguous_or_missing_inference_fails_clearly(self) -> None:
        for sql in (
            "SELECT readable_text()",
            "SELECT get_attribute('href') FROM elements a CROSS JOIN elements b",
        ):
            with self.subTest(sql=sql), self.assertRaises(HelperSqlRewriteError):
                rewrite_dom_helpers(sql)


if __name__ == "__main__":
    unittest.main()
