from __future__ import annotations

import unittest

import duckdb

from atlas_sql import AtlasCompiler
from catalogue.compiler import InteractiveQueryPurpose, compile_catalogue_sql
from catalogue.compiler.metadata import (
    BoundParameter,
    CatalogueMetadataSnapshot,
    ColumnStatistics,
    ManagedTableMetadata,
    PartitionColumn,
)
from catalogue.compiler.physical import estimate_compilation
from catalogue.compiler.purpose import ViewDefinition


class CatalogueCompilerPhysicalTests(unittest.TestCase):
    def test_estimates_partition_pruning_for_bound_parameter(self) -> None:
        estimate = estimate_compilation(
            "SELECT * FROM documents",
            "SELECT * FROM documents WHERE document_id = $document_id",
            metadata=_metadata(),
            bound_parameters=(BoundParameter("document_id", "doc-7"),),
        )

        self.assertEqual(
            estimate.executable_scans[0].partition_predicates,
            ("document_id = $document_id",),
        )
        self.assertEqual(estimate.estimated_rows_avoided, 9_990)
        self.assertEqual(estimate.estimated_bytes_avoided, 999_000)

    def test_unknown_parameter_does_not_claim_pruning(self) -> None:
        estimate = estimate_compilation(
            "SELECT * FROM documents",
            "SELECT * FROM documents WHERE document_id = $document_id",
            metadata=_metadata(),
        )

        self.assertFalse(estimate.executable_scans[0].partition_predicates)
        self.assertEqual(estimate.estimated_rows_avoided, 0)
        self.assertEqual(estimate.estimated_bytes_avoided, 0)

    def test_matches_partition_transform_and_rejects_wrong_transform(self) -> None:
        metadata = _metadata(
            partition_column=PartitionColumn("document_id", transform="year")
        )

        matching = estimate_compilation(
            "SELECT * FROM documents",
            "SELECT * FROM documents WHERE year(document_id) = $value",
            metadata=metadata,
            bound_parameters=(BoundParameter("value", 2026),),
        )
        mismatched = estimate_compilation(
            "SELECT * FROM documents",
            "SELECT * FROM documents WHERE month(document_id) = $value",
            metadata=metadata,
            bound_parameters=(BoundParameter("value", 7),),
        )

        self.assertTrue(matching.executable_scans[0].partition_predicates)
        self.assertFalse(mismatched.executable_scans[0].partition_predicates)

    def test_compilation_uses_one_definition_and_physical_snapshot(self) -> None:
        metadata = _metadata(
            views=(
                ViewDefinition(
                    schema_name="views",
                    view_name="documents",
                    sql="SELECT document_id FROM documents",
                ),
            )
        )

        result = compile_catalogue_sql(
            "SELECT document_id FROM views.documents",
            purpose=InteractiveQueryPurpose(metadata=metadata),
        )

        self.assertTrue(result.supported)
        self.assertIsNotNone(result.estimate)
        self.assertIn("documents", result.executable_sql or "")

        public = AtlasCompiler.embedded().compile(
            "SELECT document_id FROM documents",
            purpose=InteractiveQueryPurpose(metadata=metadata),
        )
        self.assertEqual(public.catalogue_revision, "lake-1")
        self.assertIsNotNone(public.estimate)

    def test_renders_partition_predicate_at_managed_scan(self) -> None:
        result = compile_catalogue_sql(
            """
            SELECT d.document_id
            FROM (
                SELECT document_id, url
                FROM documents
            ) AS d
            WHERE d.document_id = $document_id
            """,
            purpose=InteractiveQueryPurpose(
                metadata=_metadata(),
                bound_parameters=(
                    BoundParameter("document_id", "doc-7"),
                ),
            ),
        )

        self.assertTrue(result.supported)
        self.assertIn("FROM documents AS d", result.executable_sql or "")
        self.assertIn(
            "d.document_id = $document_id",
            result.executable_sql or "",
        )
        assert result.estimate is not None
        self.assertEqual(result.estimate.estimated_rows_avoided, 9_990)
        self.assertEqual(result.estimate.estimated_bytes_avoided, 999_000)
        with duckdb.connect() as connection:
            connection.execute(
                "CREATE TABLE documents(document_id VARCHAR, url VARCHAR)"
            )
            connection.execute(
                """
                INSERT INTO documents VALUES
                    ('doc-7', 'https://example.com/7'),
                    ('doc-8', 'https://example.com/8')
                """
            )
            parameters = {"document_id": "doc-7"}
            authored = connection.execute(
                result.authored_sql,
                parameters,
            ).fetchall()
            executable = connection.execute(
                result.executable_sql or "",
                parameters,
            ).fetchall()
        self.assertEqual(executable, authored)

    def test_snapshot_cannot_mix_definition_revisions(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be mixed"):
            compile_catalogue_sql(
                "SELECT 1",
                purpose=InteractiveQueryPurpose(
                    metadata=_metadata(),
                    views=(
                        ViewDefinition(
                            schema_name="views",
                            view_name="old",
                            sql="SELECT 1",
                        ),
                    ),
                ),
            )


def _metadata(
    *,
    views: tuple[ViewDefinition, ...] = (),
    partition_column: PartitionColumn = PartitionColumn("document_id"),
) -> CatalogueMetadataSnapshot:
    return CatalogueMetadataSnapshot(
        revision="lake-1",
        views=views,
        tables=(
            ManagedTableMetadata(
                schema_name="main",
                table_name="documents",
                table_uuid="table-documents",
                estimated_rows=10_000,
                file_count=100,
                file_size_bytes=1_000_000,
                partition_columns=(partition_column,),
                column_statistics=(
                    ColumnStatistics(
                        column_name="document_id",
                        distinct_count=1_000,
                    ),
                ),
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
