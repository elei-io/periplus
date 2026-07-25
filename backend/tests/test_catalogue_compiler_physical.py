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
    TableRelationship,
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

    def test_estimates_bucket_pruning_without_column_statistics(self) -> None:
        metadata = CatalogueMetadataSnapshot(
            revision="lake-1",
            tables=(
                ManagedTableMetadata(
                    schema_name="main",
                    table_name="elements",
                    table_uuid="table-elements",
                    estimated_rows=6_400,
                    file_count=64,
                    file_size_bytes=640_000,
                    partition_columns=(
                        PartitionColumn(
                            "document_id",
                            transform="bucket",
                            bucket_count=64,
                        ),
                    ),
                ),
            ),
        )

        estimate = estimate_compilation(
            "SELECT * FROM elements",
            """
            SELECT *
            FROM elements
            WHERE document_id = UUID '00000000-0000-0000-0000-000000000007'
            """,
            metadata=metadata,
        )

        self.assertEqual(
            estimate.executable_scans[0].partition_predicates,
            (
                "document_id = "
                "CAST('00000000-0000-0000-0000-000000000007' AS UUID)",
            ),
        )
        self.assertEqual(estimate.estimated_rows_avoided, 6_300)
        self.assertEqual(estimate.estimated_bytes_avoided, 630_000)

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

    def test_recognizes_parenthesized_partition_range(self) -> None:
        metadata = _metadata(
            partition_column=PartitionColumn("captured_at", transform="day")
        )

        estimate = estimate_compilation(
            "SELECT * FROM documents",
            """
            SELECT *
            FROM documents
            WHERE (
                captured_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00'
                AND captured_at < TIMESTAMPTZ '2026-08-01 00:00:00+00'
            )
            """,
            metadata=metadata,
        )

        self.assertEqual(
            len(estimate.executable_scans[0].partition_predicates),
            2,
        )
        self.assertEqual(estimate.estimated_rows_avoided, 9_375)
        self.assertEqual(estimate.estimated_bytes_avoided, 937_500)

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

    def test_selective_document_lineage_builds_a_staged_element_scope(self) -> None:
        result = compile_catalogue_sql(
            """
            WITH latest_pages AS (
                SELECT document_id, url, completed_at
                FROM crawls
                WHERE registrable_domain = 'example.com'
                  AND completed_at >= TIMESTAMPTZ '2026-07-18'
                QUALIFY row_number() OVER (
                    PARTITION BY url ORDER BY completed_at DESC
                ) = 1
            ),
            products AS (
                SELECT page.document_id, page.url,
                       product.element_index,
                       product.subtree_end_index
                FROM latest_pages AS page
                JOIN elements AS product USING (document_id)
                WHERE product.attributes['itemscope'] IS NOT NULL
            )
            SELECT product.url, property.text_direct
            FROM products AS product
            JOIN elements AS property
              ON property.document_id = product.document_id
             AND property.element_index BETWEEN product.element_index
                                                AND product.subtree_end_index
            LIMIT 100
            """,
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )

        self.assertTrue(result.supported)
        self.assertIsNotNone(result.document_scope)
        assert result.document_scope is not None
        self.assertEqual(result.document_scope.maximum_documents, 10_000)
        self.assertEqual(result.document_scope.maximum_elements, 50_000_000)
        self.assertEqual(
            result.document_scope.element_columns,
            (
                "document_id",
                "element_index",
                "subtree_end_index",
                "attributes",
                "text_direct",
            ),
        )
        self.assertIn("FROM crawls", result.document_scope.scope_sql)
        self.assertIn(
            "registrable_domain = 'example.com'",
            result.document_scope.scope_sql,
        )
        self.assertNotIn("JOIN documents", result.document_scope.scope_sql)
        self.assertIn(
            "_atlas_interactive_scoped_elements",
            result.executable_sql or "",
        )
        self.assertNotIn("FROM elements", result.executable_sql or "")
        self.assertEqual(
            result.applied_rewrites[-1].rule,
            "bounded_document_scan_scope",
        )
        assert result.estimate is not None
        scoped_scan = result.estimate.executable_scans[-1]
        self.assertEqual(scoped_scan.relation, "main.elements")
        self.assertEqual(scoped_scan.estimated_rows_read, 50_000_000)
        self.assertEqual(
            scoped_scan.partition_predicates,
            ("runtime document_id scope (at most 10,000 documents)",),
        )
        public = AtlasCompiler.embedded().compile(
            result.authored_sql,
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )
        self.assertIsNotNone(public.document_scope)
        assert public.document_scope is not None
        self.assertEqual(
            public.document_scope.element_columns,
            result.document_scope.element_columns,
        )

    def test_literal_document_scope_keeps_a_single_stage_element_scan(self) -> None:
        result = compile_catalogue_sql(
            """
            SELECT element_index, tag
            FROM elements
            WHERE document_id = 'sha256:one'
            ORDER BY element_index
            LIMIT 100
            """,
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )

        self.assertTrue(result.supported)
        self.assertIsNone(result.document_scope)
        self.assertIn("FROM elements", result.executable_sql or "")

    def test_staged_scope_includes_an_independent_literal_element_branch(
        self,
    ) -> None:
        result = compile_catalogue_sql(
            """
            WITH selected AS (
                SELECT document_id
                FROM crawls
                WHERE registrable_domain = 'example.com'
            ),
            derived AS (
                SELECT element.document_id, element.tag
                FROM selected
                JOIN elements AS element USING (document_id)
            )
            SELECT document_id, tag FROM derived
            UNION ALL
            SELECT document_id, tag
            FROM elements
            WHERE document_id = 'sha256:manual'
            """,
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )

        self.assertTrue(result.supported)
        self.assertIsNotNone(result.document_scope)
        assert result.document_scope is not None
        self.assertIn("'sha256:manual'", result.document_scope.scope_sql)

    def test_unbounded_element_scan_is_not_executable(self) -> None:
        result = compile_catalogue_sql(
            "SELECT attributes FROM elements LIMIT 100",
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )

        self.assertTrue(result.valid)
        self.assertFalse(result.supported)
        self.assertIsNone(result.executable_sql)
        self.assertEqual(result.diagnostics[-1].code, "unbounded_relation")

    def test_staged_document_scope_preserves_query_rows(self) -> None:
        sql = """
            WITH selected AS (
                SELECT document_id, url
                FROM crawls
                WHERE registrable_domain = 'example.com'
            )
            SELECT selected.url, element.element_index, element.tag
            FROM selected
            JOIN elements AS element USING (document_id)
            WHERE element.tag IN ('title', 'h1')
            ORDER BY selected.url, element.element_index
        """
        result = compile_catalogue_sql(
            sql,
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )
        assert result.document_scope is not None
        assert result.executable_sql is not None

        with duckdb.connect() as connection:
            connection.execute(
                "CREATE TABLE crawls("
                "document_id VARCHAR, url VARCHAR, registrable_domain VARCHAR)"
            )
            connection.execute(
                "CREATE TABLE documents("
                "document_id VARCHAR, element_count BIGINT)"
            )
            connection.execute(
                "CREATE TABLE elements("
                "document_id VARCHAR, element_index BIGINT, tag VARCHAR)"
            )
            connection.execute(
                "INSERT INTO crawls VALUES "
                "('doc-1', 'https://example.com/a', 'example.com'), "
                "('doc-2', 'https://other.test/b', 'other.test')"
            )
            connection.execute(
                "INSERT INTO documents VALUES ('doc-1', 3), ('doc-2', 1)"
            )
            connection.execute(
                "INSERT INTO elements VALUES "
                "('doc-1', 0, 'html'), "
                "('doc-1', 1, 'title'), "
                "('doc-1', 2, 'h1'), "
                "('doc-2', 0, 'title')"
            )
            scope_rows = connection.execute(
                result.document_scope.scope_sql
            ).fetchall()
            values = ", ".join(
                f"('{row[0]}')"
                for row in scope_rows
            )
            connection.execute(
                "CREATE TEMP TABLE _atlas_interactive_scoped_elements AS "
                "SELECT element.document_id, element.element_index, element.tag "
                "FROM elements AS element "
                f"JOIN (VALUES {values}) AS scope(document_id) USING (document_id)"
            )

            authored = connection.execute(sql).fetchall()
            executable = connection.execute(result.executable_sql).fetchall()

        self.assertEqual(executable, authored)
        self.assertEqual(
            executable,
            [
                ("https://example.com/a", 1, "title"),
                ("https://example.com/a", 2, "h1"),
            ],
        )

    def test_volatile_driver_filter_is_excluded_from_the_safe_scope(self) -> None:
        result = compile_catalogue_sql(
            """
            WITH selected AS (
                SELECT document_id
                FROM crawls
                WHERE registrable_domain = 'example.com'
                  AND random() < 0.5
            )
            SELECT element.tag
            FROM selected
            JOIN elements AS element USING (document_id)
            """,
            purpose=InteractiveQueryPurpose(metadata=_dom_metadata()),
        )

        self.assertTrue(result.supported)
        self.assertIsNotNone(result.document_scope)
        assert result.document_scope is not None
        self.assertNotIn("RANDOM", result.document_scope.scope_sql.upper())
        self.assertIn("RANDOM", (result.executable_sql or "").upper())


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


def _dom_metadata() -> CatalogueMetadataSnapshot:
    document_relationship = TableRelationship(
        columns=("document_id",),
        target_schema="main",
        target_table="documents",
        target_columns=("document_id",),
    )
    return CatalogueMetadataSnapshot(
        revision="lake-dom",
        tables=(
            ManagedTableMetadata(
                schema_name="main",
                table_name="crawls",
                table_uuid="table-crawls",
                estimated_rows=1_500_000,
                partition_columns=(
                    PartitionColumn("completed_at", transform="day"),
                ),
                relationships=(
                    TableRelationship(
                        columns=("document_id",),
                        target_schema="main",
                        target_table="documents",
                        target_columns=("document_id",),
                        optional=True,
                    ),
                ),
            ),
            ManagedTableMetadata(
                schema_name="main",
                table_name="documents",
                table_uuid="table-documents",
                estimated_rows=1_365_000,
                stable_key=("document_id",),
                partition_columns=(
                    PartitionColumn(
                        "document_id",
                        transform="bucket",
                        bucket_count=64,
                    ),
                ),
            ),
            ManagedTableMetadata(
                schema_name="main",
                table_name="elements",
                table_uuid="table-elements",
                estimated_rows=13_650_000_000,
                stable_key=("document_id", "element_index"),
                partition_columns=(
                    PartitionColumn(
                        "document_id",
                        transform="bucket",
                        bucket_count=64,
                    ),
                ),
                relationships=(document_relationship,),
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
