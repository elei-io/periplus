"""Real-SQL equivalence and bounded activation for selected capture links."""

from pathlib import Path
from unittest.mock import patch
import unittest
import re
from uuid import UUID

from element_fixture import catalogue
from periplus.query.compiler import compile_query
from periplus.query.models import QueryRequest
from periplus.query.optimizations.base import PassContext
from periplus.query.optimizations.capture_links import CAPTURE_LINKS, match, run
from periplus.query.validation import _one_statement
from periplus.urls import normalize_url

SQL = """WITH selected AS (
 SELECT capture_id FROM experimental.capture
 WHERE page_url = 'http://example.com/start'
 ORDER BY captured_at DESC, capture_id DESC LIMIT 1
)
SELECT l.target_url, count(*) AS occurrences
FROM experimental.link l JOIN selected s ON s.capture_id=l.capture_id
GROUP BY l.target_url ORDER BY occurrences DESC,l.target_url LIMIT 50"""


class CaptureLinkScopeTests(unittest.TestCase):
    def setUp(self):
        self.db = catalogue().connection
        self.addCleanup(self.db.close)
        self.db.execute("SET schema='experimental'")
        for number, url, effective, date, media in [
            (
                1,
                "http://example.com/start",
                "https://example.com/old",
                "2026-01-01",
                "text/html",
            ),
            (
                2,
                "http://example.com/start",
                "https://example.com/tie",
                "2026-01-02",
                "text/html",
            ),
            (
                3,
                "http://example.com/start",
                " HTTPS://Example.com:443/final#fragment ",
                "2026-01-02",
                "text/html",
            ),
            (4, "https://example.com/quote'", None, "2026-01-03", "text/html"),
            (
                5,
                "http://example.com/start",
                "https://example.com/nonhtml",
                "2026-01-04",
                "application/json",
            ),
        ]:
            visit, document = UUID(int=number), UUID(int=number + 100)
            self.db.execute(
                "INSERT INTO ingest.visits (visit_id,document_id,requested_url,effective_url,observed_at) VALUES (?,?,?,?,?::TIMESTAMPTZ)",
                [visit, document, url, effective, date],
            )
            self.db.execute(
                "INSERT INTO ingest.documents (visit_id,document_id,detected_media_type,content_sha256) VALUES (?,?,?,?)",
                [visit, document, media, str(number)],
            )
            for node, target in enumerate(
                ["https://target/a", "https://target/a", "https://target/b"]
            ):
                self.db.execute(
                    "INSERT INTO material.link_occurrences (visit_id,element_index,source_url,target_url,raw_href) VALUES (?,?,?,?,?)",
                    [
                        visit,
                        node,
                        normalize_url(effective or url),
                        target + str(number),
                        "/target",
                    ],
                )

    def context(
        self, sql=SQL, *, connection=True, schema="experimental", parameters=None
    ):
        return PassContext(
            _one_statement(sql),
            parameters or [],
            schema,
            "memory",
            self.db if connection else None,
        )

    def assert_equivalent(self, sql=SQL, schema="experimental"):
        expected = self.db.execute(sql).fetchall()
        description = self.db.description
        decision = run(self.context(sql, schema=schema))
        self.assertEqual(decision.status, "applied", decision)
        actual = self.db.execute(decision.statement.sql(dialect="duckdb")).fetchall()
        self.assertEqual(actual, expected)
        self.assertEqual(self.db.description, description)
        return decision

    def test_original_case_shape_redirect_ties_and_duplicates(self):
        decision = self.assert_equivalent()
        self.assertEqual(decision.counts, {"captures": 1})
        self.assertEqual(
            self.db.execute(SQL).fetchall(),
            [("https://target/a3", 2), ("https://target/b3", 1)],
        )
        generated = decision.statement.sql(dialect="duckdb")
        self.assertIn("https://example.com/final", generated)
        self.assertIn("source_url IN", generated)
        self.assertIn("visit_id IN", generated)
        self.assertNotIn("fragment", generated)

    def test_multiple_keys_empty_scope_null_fallback_and_projection(self):
        for sql in (
            SQL.replace("DESC LIMIT 1", "DESC LIMIT 3"),
            SQL.replace(
                "= 'http://example.com/start'",
                "IN ('http://example.com/start', 'https://example.com/quote''')",
            ).replace("DESC LIMIT 1", "DESC LIMIT 4"),
            SQL.replace("http://example.com/start", "https://absent/"),
            SQL.replace("DESC LIMIT 1", "DESC LIMIT 0"),
            SQL.replace(
                "SELECT l.target_url, count(*) AS occurrences",
                "SELECT l.*, s.capture_id AS selected_id",
            ).replace(
                "GROUP BY l.target_url ORDER BY occurrences DESC,l.target_url",
                "ORDER BY l.capture_id,l.node_index",
            ),
            SQL.replace(
                "FROM experimental.link l JOIN selected s",
                "FROM selected s JOIN experimental.link l",
            ),
        ):
            with self.subTest(sql=sql):
                self.assert_equivalent(sql)
        self.assert_equivalent(
            SQL.replace("experimental.", "public_v1."), schema="public_v1"
        )

    def test_qualified_selection_and_quoted_aliases(self):
        sql = (
            SQL.replace(
                "SELECT capture_id FROM experimental.capture",
                "SELECT c.capture_id FROM experimental.capture c",
            )
            .replace("WHERE page_url", "WHERE c.page_url")
            .replace(
                "ORDER BY captured_at DESC, capture_id DESC",
                "ORDER BY c.captured_at DESC, c.capture_id DESC",
            )
        )
        self.assert_equivalent(sql)
        self.assert_equivalent(
            re.sub(
                r"\bl\.", '"link alias".', SQL.replace("link l", 'link "link alias"')
            )
        )

    def test_unsupported_shapes_do_not_lookup(self):
        queries = [
            SQL.replace(" JOIN selected", " LEFT JOIN selected"),
            SQL.replace("DESC LIMIT 1", "DESC LIMIT 1 OFFSET 1"),
            SQL.replace("captured_at DESC, capture_id DESC", "random()"),
            SQL.replace("captured_at DESC, capture_id DESC", "captured_at DESC"),
            SQL.replace("SELECT capture_id FROM", "SELECT DISTINCT capture_id FROM"),
            SQL.replace("= 'http://example.com/start'", "LIKE '%example%'"),
            SQL.replace("selected AS (", "selected(capture_id) AS ("),
            SQL.replace("DESC LIMIT 1", "DESC LIMIT ?"),
            SQL.replace(
                "s.capture_id=l.capture_id", "s.capture_id=l.capture_id OR true"
            ),
            SQL.replace(
                "SELECT l.target_url",
                "SELECT (SELECT max(capture_id) FROM experimental.capture), l.target_url",
            ),
            SQL.replace(
                "SELECT capture_id FROM experimental.capture",
                "SELECT capture_id FROM experimental.capture TABLESAMPLE 50%",
            ),
        ]
        with patch("periplus.query.optimizations.capture_links.lookup_keys") as lookup:
            for sql in queries:
                with self.subTest(sql=sql):
                    self.assertIsNone(match(self.context(sql)))
            self.assertIsNone(match(self.context(parameters=["x"])))
            lookup.assert_not_called()

    def test_budgets_decline_without_partial_answer(self):
        sql = SQL.replace("DESC LIMIT 1", "DESC LIMIT 3")
        for constant, value, reason in [
            ("MAX_CAPTURES", 1, "captures"),
            ("MAX_URL_BYTES", 2, "source_url"),
            ("MAX_KEY_BYTES", 2, "key_bytes"),
        ]:
            with patch("periplus.query.optimizations.capture_links." + constant, value):
                decision = run(self.context(sql))
                self.assertEqual(
                    (decision.status, decision.reason), ("budget_exceeded", reason)
                )
                self.assertIsNone(decision.statement)

    def test_contract_changes_and_invalid_sources_decline(self):
        self.db.execute(
            "CREATE OR REPLACE VIEW experimental.link AS SELECT visit_id AS capture_id,element_index AS node_index,target_url,raw_href FROM material.link_occurrences"
        )
        with patch("periplus.query.optimizations.capture_links.lookup_keys") as lookup:
            self.assertEqual(run(self.context()).status, "contract_mismatch")
            lookup.assert_not_called()

    def test_changed_capture_semantics_and_invalid_url_decline(self):
        self.db.execute(
            "UPDATE ingest.visits SET effective_url='file:///private' WHERE visit_id=?",
            [UUID(int=3)],
        )
        self.assertEqual(run(self.context()).status, "contract_mismatch")
        self.db.execute(
            "CREATE OR REPLACE VIEW experimental.capture AS SELECT visit_id AS capture_id,requested_url AS page_url,effective_url,observed_at AS captured_at FROM ingest.visits"
        )
        with patch("periplus.query.optimizations.capture_links.lookup_keys") as lookup:
            self.assertEqual(run(self.context()).reason, "views")
            lookup.assert_not_called()

    def test_frozen_selection_keeps_duplicate_capture_membership(self):
        # Defensive equivalence even if duplicate evidence yields duplicate capture rows.
        self.db.execute(
            "INSERT INTO ingest.documents SELECT * FROM ingest.documents WHERE visit_id=?",
            [UUID(int=3)],
        )
        decision = self.assert_equivalent(SQL.replace("DESC LIMIT 1", "DESC LIMIT 3"))
        self.assertEqual(decision.counts["captures"], 3)

    def test_collation_declines(self):
        self.db.execute("SET default_collation='nocase'")
        self.assertEqual(run(self.context()).status, "contract_mismatch")

    def test_preparation_activation_and_original_ast_preservation(self):
        context = self.context()
        original = context.statement.sql()
        with patch("periplus.query.optimizations.capture_links.lookup_keys") as lookup:
            prepared = compile_query(
                self.db,
                QueryRequest(sql=SQL),
                schema="experimental",
                catalogue_alias="memory",
                execute=False,
                max_rows=100,
                passes=(CAPTURE_LINKS,),
            )
            lookup.assert_not_called()
        self.assertEqual(prepared.optimizations, [])
        self.assertIn("deferred.capture_lookup", prepared.diagnostics[0].code)
        run(context)
        self.assertEqual(context.statement.sql(), original)
        expected = self.db.execute(SQL).fetchall()
        compiled = compile_query(
            self.db,
            QueryRequest(sql=SQL),
            schema="experimental",
            catalogue_alias="memory",
            execute=True,
            max_rows=100,
            passes=(CAPTURE_LINKS,),
        )
        self.assertEqual(compiled.optimizations, ["capture_link_scope"])
        self.assertEqual(self.db.execute(compiled.executable_sql).fetchall(), expected)
        self.assertNotIn("example.com", compiled.diagnostics[0].message)

    def test_business_case_is_recognized(self):
        sql = (
            Path(__file__).resolve().parents[3]
            / "benchmarks/query/cases/single-capture-links/query.sql"
        ).read_text()
        self.assertIsNotNone(match(self.context(sql)))
