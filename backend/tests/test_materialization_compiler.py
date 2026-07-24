from __future__ import annotations

import unittest

from materialization.compiler import (
    IncompatibilityCode,
    IncompatibleQueryError,
    compile_materialization,
)


class MaterializationCompilerDeveloperExperienceTests(unittest.TestCase):
    def test_first_supported_plan_and_incompatibility_are_actionable(self) -> None:
        plan = compile_materialization(
            sql=(
                "SELECT document_id, captured_at "
                "FROM documents "
                "WHERE captured_at IS NOT NULL"
            ),
            source_table="documents",
            refresh_strategy="keyed",
            key_columns=("document_id",),
        )

        self.assertEqual(plan.key_columns, ("document_id",))
        self.assertEqual(plan.output_columns, ("document_id", "captured_at"))
        self.assertEqual(plan.scans[0].relation, "documents")
        self.assertEqual(
            plan.explain(),
            "keyed materialization supported\n"
            "Driving table: documents\n"
            "Stable key: document_id\n"
            "Bounded scans:\n"
            "  documents AS documents by document_id",
        )

        with self.assertRaises(IncompatibleQueryError) as raised:
            compile_materialization(
                sql=(
                    "SELECT document.document_id "
                    "FROM documents AS document "
                    "JOIN elements AS element USING (document_id)"
                ),
                source_table="documents",
                refresh_strategy="keyed",
                key_columns=("document_id",),
            )

        error = raised.exception
        self.assertEqual(
            error.code,
            IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
        )
        self.assertEqual(
            error.as_dict(),
            {
                "code": IncompatibilityCode.UNSUPPORTED_QUERY_SHAPE,
                "message": "Queries containing joins are not supported yet.",
                "sql_fragment": "JOIN elements AS element USING (document_id)",
                "documentation_anchor": "supported-subset",
            },
        )
        self.assertEqual(
            str(error),
            "[unsupported_query_shape] "
            "Queries containing joins are not supported yet.",
        )


if __name__ == "__main__":
    unittest.main()
