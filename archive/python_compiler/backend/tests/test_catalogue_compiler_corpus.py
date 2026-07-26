from __future__ import annotations

import unittest

import duckdb

from catalogue.compiler import (
    InteractiveQueryPurpose,
    SqlCompilationOutcome,
    compile_catalogue_sql,
)


class CatalogueCompilerCombinationCorpusTests(unittest.TestCase):
    """Generated combinations of common SQL shapes must remain equivalent."""

    def test_projection_join_and_filter_combinations(self) -> None:
        with duckdb.connect() as connection:
            _seed(connection)
            for wrapper in ("derived", "cte"):
                for predicate in (
                    "d.document_id = 2",
                    "d.document_id >= 2",
                    "d.document_id IN (1, 3)",
                    "lower(d.url) = 'https://b.example'",
                ):
                    for join in ("JOIN", "INNER JOIN"):
                        sql = _query(
                            wrapper=wrapper,
                            predicate=predicate,
                            join=join,
                        )
                        with self.subTest(
                            wrapper=wrapper,
                            predicate=predicate,
                            join=join,
                        ):
                            result = compile_catalogue_sql(
                                sql,
                                purpose=InteractiveQueryPurpose(),
                            )
                            self.assertNotEqual(
                                result.outcome,
                                SqlCompilationOutcome.INVALID,
                            )
                            authored = connection.execute(sql).fetchall()
                            executable = connection.execute(
                                result.executable_sql or "",
                            ).fetchall()
                            self.assertEqual(executable, authored)

    def test_unknown_function_is_a_rewrite_barrier_not_validity_gate(self) -> None:
        with duckdb.connect() as connection:
            _seed(connection)
            connection.create_function(
                "user_score",
                lambda value: value * 10,
                ["INTEGER"],
                "INTEGER",
            )
            sql = """
                SELECT d.document_id
                FROM (
                    SELECT document_id
                    FROM documents
                ) AS d
                WHERE user_score(d.document_id) >= 20
                ORDER BY d.document_id
            """

            result = compile_catalogue_sql(
                sql,
                purpose=InteractiveQueryPurpose(),
            )

            self.assertTrue(result.valid)
            self.assertEqual(
                connection.execute(result.executable_sql or "").fetchall(),
                connection.execute(sql).fetchall(),
            )

    def test_valid_duckdb_pivot_syntax_is_an_authored_fallback(self) -> None:
        with duckdb.connect() as connection:
            _seed(connection)
            for sql in (
                (
                    "PIVOT documents ON url USING count(*) "
                    "GROUP BY document_id"
                ),
                (
                    "UNPIVOT (SELECT CAST(document_id AS VARCHAR) AS "
                    "document_id, url FROM documents) ON document_id, url "
                    "INTO NAME source VALUE value"
                ),
                (
                    "PIVOT documents ON url USING count(*) "
                    "GROUP BY document_id ORDER BY document_id"
                ),
                (
                    "UNPIVOT (SELECT CAST(document_id AS VARCHAR) AS "
                    "document_id, url FROM documents) ON document_id, url "
                    "INTO NAME source VALUE value ORDER BY source"
                ),
            ):
                with self.subTest(sql=sql):
                    authored = connection.execute(sql).fetchall()
                    result = compile_catalogue_sql(
                        sql,
                        purpose=InteractiveQueryPurpose(),
                    )
                    self.assertEqual(
                        result.outcome,
                        SqlCompilationOutcome.UNSUPPORTED,
                    )
                    self.assertEqual(result.executable_sql, sql)
                    self.assertEqual(
                        connection.execute(result.executable_sql).fetchall(),
                        authored,
                    )


def _query(*, wrapper: str, predicate: str, join: str) -> str:
    source = "SELECT document_id, url FROM documents"
    if wrapper == "cte":
        return f"""
            WITH selected AS ({source})
            SELECT d.document_id, e.element_index
            FROM selected AS d
            {join} elements AS e
              ON e.document_id = d.document_id
            WHERE {predicate}
            ORDER BY d.document_id, e.element_index
        """
    return f"""
        SELECT d.document_id, e.element_index
        FROM ({source}) AS d
        {join} elements AS e
          ON e.document_id = d.document_id
        WHERE {predicate}
        ORDER BY d.document_id, e.element_index
    """


def _seed(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        "CREATE TABLE documents(document_id INTEGER, url VARCHAR)"
    )
    connection.execute(
        """
        INSERT INTO documents VALUES
            (1, 'https://a.example'),
            (2, 'https://b.example'),
            (3, 'https://c.example')
        """
    )
    connection.execute(
        "CREATE TABLE elements(document_id INTEGER, element_index INTEGER)"
    )
    connection.execute(
        "INSERT INTO elements VALUES (1, 0), (2, 0), (2, 1), (3, 0)"
    )


if __name__ == "__main__":
    unittest.main()
