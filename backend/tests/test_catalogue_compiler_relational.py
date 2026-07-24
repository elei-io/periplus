from __future__ import annotations

import unittest
import random

from sqlglot import exp, parse_one

from catalogue.compiler.analysis import analyze_resolved_query
from catalogue.compiler.relational import RelationColumn


class CatalogueCompilerRelationalTests(unittest.TestCase):
    def test_collects_direct_projection_lineage(self) -> None:
        analysis = _analyze(
            "SELECT d.document_id AS id, d.url FROM documents AS d"
        )

        facts = analysis.relational_facts[-1]

        self.assertEqual(
            tuple(
                (projection.output, projection.source)
                for projection in facts.direct_projections
            ),
            (
                ("id", RelationColumn("d", "document_id")),
                ("url", RelationColumn("d", "url")),
            ),
        )

    def test_inner_join_equivalence_is_transitive(self) -> None:
        analysis = _analyze(
            """
            SELECT d.document_id
            FROM documents AS d
            JOIN elements AS e ON e.document_id = d.document_id
            JOIN attributes AS a USING (document_id)
            """
        )

        equivalents = analysis.relational_facts[-1].equivalents(
            RelationColumn("d", "document_id")
        )

        self.assertEqual(
            equivalents,
            frozenset(
                {
                    RelationColumn("d", "document_id"),
                    RelationColumn("e", "document_id"),
                    RelationColumn("a", "document_id"),
                }
            ),
        )

    def test_direct_lineage_crosses_nested_scopes(self) -> None:
        analysis = _analyze(
            """
            WITH renamed AS (
                SELECT d.document_id AS inner_id
                FROM documents AS d
            )
            SELECT r.inner_id AS result_id
            FROM renamed AS r
            """
        )
        inner, outer = analysis.scopes

        self.assertTrue(
            analysis.column_lineage.connected(
                (id(inner), "d", "document_id"),
                (id(outer), "$output", "result_id"),
            )
        )

    def test_outer_join_records_nullability_without_equivalence(self) -> None:
        analysis = _analyze(
            """
            SELECT d.document_id
            FROM documents AS d
            LEFT JOIN elements AS e
              ON e.document_id = d.document_id
            """
        )

        facts = analysis.relational_facts[-1]

        self.assertEqual(facts.nullable_relations, frozenset({"e"}))
        self.assertFalse(facts.equivalence_classes)

    def test_equivalence_graph_is_invariant_under_conjunct_order(self) -> None:
        randomizer = random.Random(7)
        first_join = "e.document_id = d.document_id"
        second_join = [
            "a.document_id = e.document_id",
            "a.tenant_id = d.tenant_id",
        ]
        expected = frozenset(
            {
                RelationColumn("d", "document_id"),
                RelationColumn("e", "document_id"),
                RelationColumn("a", "document_id"),
            }
        )

        for _ in range(50):
            randomizer.shuffle(second_join)
            oriented = [
                (
                    " = ".join(reversed(predicate.split(" = ")))
                    if randomizer.choice((False, True))
                    else predicate
                )
                for predicate in second_join
            ]
            oriented_first = (
                " = ".join(reversed(first_join.split(" = ")))
                if randomizer.choice((False, True))
                else first_join
            )
            analysis = _analyze(
                """
                SELECT d.document_id
                FROM documents AS d
                JOIN elements AS e
                  ON """
                + oriented_first
                + """
                JOIN attributes AS a
                  ON """
                + " AND ".join(oriented)
            )

            equivalents = analysis.relational_facts[-1].equivalents(
                RelationColumn("d", "document_id")
            )
            self.assertEqual(equivalents, expected)


def _analyze(sql: str):
    query = parse_one(sql, dialect="duckdb")
    assert isinstance(query, exp.Query)
    return analyze_resolved_query(query)


if __name__ == "__main__":
    unittest.main()
