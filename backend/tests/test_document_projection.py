import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from atlas.materialization.document_projection import (
    CONTENT_STATS_SCHEMA,
    HTML_ELEMENT_SCHEMA,
    JSONLD_SCHEMA,
    LINK_OCCURRENCE_SCHEMA,
    LINK_SCHEMA,
    DocumentObservation,
    DocumentProjectionSource,
    ducklake_varchar_bucket,
    project_documents,
)
from atlas.materialization.document_workload import (
    DocumentProjectionOutput,
    combine_document_outputs,
    partition_document_output,
)
from atlas.platform.catalogue.schema import PARTITION_BUCKETS


class DocumentProjectionTests(unittest.TestCase):
    def test_ducklake_bucket_uses_iceberg_murmur3(self) -> None:
        # Murmur3_x86_32("foo") == 0xf6a5c420.
        self.assertEqual(
            ducklake_varchar_bucket("foo", 64),
            (0xF6A5C420 & 0x7FFFFFFF) % 64,
        )

    def test_one_read_and_parse_produces_every_document_projection(self) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = """
        <html><head>
          <script type="application/ld+json">
            {"@type":"Article","name":"Example"}
          </script>
        </head><body><a href="/next">Next</a></body></html>
        """
        observed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

        projection = project_documents(
            repository,
            (
                DocumentProjectionSource(
                    content_sha256="abc",
                    object_key="objects/abc",
                    storage_encoding="zstd",
                    content_bytes=200,
                    observations=(
                        DocumentObservation(
                            visit_id=(
                                "7c1f63ab-42c9-47d8-8221-54127e15c5e7"
                            ),
                            document_id=(
                                "cd7ea411-330c-5492-93ac-f804eb2a3859"
                            ),
                            source_url="https://example.com/base",
                            observed_at=observed_at,
                        ),
                    ),
                ),
            ),
        )

        repository.read.assert_called_once_with("objects/abc")
        self.assertEqual(projection.content_stats.schema, CONTENT_STATS_SCHEMA)
        self.assertEqual(projection.html_elements.schema, HTML_ELEMENT_SCHEMA)
        self.assertEqual(projection.jsonld_values.schema, JSONLD_SCHEMA)
        self.assertEqual(projection.links.schema, LINK_SCHEMA)
        self.assertEqual(
            projection.link_occurrences.schema,
            LINK_OCCURRENCE_SCHEMA,
        )
        self.assertEqual(projection.content_stats.num_rows, 1)
        self.assertGreater(projection.html_elements.num_rows, 0)
        self.assertEqual(projection.jsonld_values.num_rows, 1)
        self.assertEqual(projection.links.num_rows, 1)
        self.assertEqual(projection.link_occurrences.num_rows, 1)
        self.assertEqual(
            projection.jsonld_values["type_terms"].to_pylist(),
            [["Article"]],
        )
        self.assertEqual(
            projection.links["target_url"].to_pylist(),
            ["https://example.com/next"],
        )
        document = projection.content_stats.to_pylist()[0]
        self.assertEqual(document["content_sha256"], "abc")
        self.assertEqual(
            document["dom_element_count"],
            projection.html_elements.num_rows,
        )
        self.assertGreaterEqual(document["dom_max_depth"], 2)
        self.assertNotIn("nodes", document)
        self.assertEqual(
            projection.link_occurrences["visit_id"].to_pylist(),
            ["7c1f63ab-42c9-47d8-8221-54127e15c5e7"],
        )
        self.assertGreater(
            sum(
                table.nbytes
                for table in (
                    projection.content_stats,
                    projection.html_elements,
                    projection.jsonld_values,
                    projection.links,
                    projection.link_occurrences,
                )
            ),
            0,
        )

    def test_projection_is_split_once_by_projected_arrow_bytes(self) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = (
            "<html><body><a href='/next'>Next</a></body></html>"
        )
        observed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        sources = tuple(
            DocumentProjectionSource(
                content_sha256=f"hash-{index}",
                object_key=f"objects/{index}",
                storage_encoding="zstd",
                content_bytes=100,
                observations=(
                    DocumentObservation(
                        visit_id=(
                            f"10000000-0000-0000-0000-{index:012d}"
                        ),
                        document_id=(
                            f"00000000-0000-0000-0000-{index:012d}"
                        ),
                        source_url=f"https://example.com/{index}",
                        observed_at=observed_at,
                    ),
                ),
            )
            for index in range(16)
        )
        projection = project_documents(repository, sources)
        enabled_targets = frozenset(
            {
                "content_stats",
                "html_elements",
                "jsonld_values",
                "links",
                "link_occurrences",
            }
        )

        partitions = partition_document_output(
            DocumentProjectionOutput(projection=projection),
            enabled_targets=enabled_targets,
            target_bytes=max(
                1,
                projection.bytes_for(enabled_targets) // 2,
            ),
            max_partitions=2,
        )

        self.assertEqual(len(partitions), 2)
        split_bytes = sum(
            partition.projection.bytes_for(enabled_targets)
            for partition in partitions
            if partition.projection is not None
        )
        self.assertGreaterEqual(
            split_bytes,
            projection.bytes_for(enabled_targets),
        )
        self.assertLess(
            split_bytes,
            projection.bytes_for(enabled_targets) * 1.1,
        )
        repository.read.assert_has_calls(
            [
                call(f"objects/{index}")
                for index in range(16)
            ]
        )
        for table_name in (
            "content_stats",
            "html_elements",
            "jsonld_values",
            "links",
            "link_occurrences",
        ):
            self.assertEqual(
                sum(
                    getattr(partition.projection, table_name).num_rows
                    for partition in partitions
                    if partition.projection is not None
                ),
                getattr(projection, table_name).num_rows,
            )
        for partition in partitions:
            self.assertIsNotNone(partition.projection)
            split = partition.projection
            assert split is not None
            self.assertEqual(
                set(split.links["link_id"].to_pylist()),
                set(split.link_occurrences["link_id"].to_pylist()),
            )

        shadow_partitions = partition_document_output(
            DocumentProjectionOutput(projection=projection),
            enabled_targets=enabled_targets,
            target_bytes=max(
                1,
                projection.bytes_for(enabled_targets) // 2,
            ),
            max_partitions=2,
            partition_shadow_links_by_source=True,
        )
        self.assertEqual(len(shadow_partitions), 2)
        for partition_index, partition in enumerate(shadow_partitions):
            assert partition.projection is not None
            for source_page_id in partition.projection.links[
                "source_page_id"
            ].to_pylist():
                self.assertEqual(
                    ducklake_varchar_bucket(
                        str(source_page_id),
                        PARTITION_BUCKETS,
                    )
                    % 2,
                    partition_index,
                )

    def test_projector_outputs_coalesce_duplicate_link_dimensions(
        self,
    ) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = (
            "<html><body><a href='/next'>Next</a></body></html>"
        )
        observed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        outputs = tuple(
            DocumentProjectionOutput(
                projection=project_documents(
                    repository,
                    (
                        DocumentProjectionSource(
                            content_sha256=f"hash-{index}",
                            object_key=f"objects/{index}",
                            storage_encoding="zstd",
                            content_bytes=100,
                            observations=(
                                DocumentObservation(
                                    visit_id=(
                                        "10000000-0000-0000-0000-"
                                        f"{index:012d}"
                                    ),
                                    document_id=(
                                        "00000000-0000-0000-0000-"
                                        f"{index:012d}"
                                    ),
                                    source_url="https://example.com/base",
                                    observed_at=observed_at,
                                ),
                            ),
                        ),
                    ),
                )
            )
            for index in range(2)
        )

        combined = combine_document_outputs(outputs)

        self.assertIsNotNone(combined.projection)
        assert combined.projection is not None
        self.assertEqual(combined.projection.links.num_rows, 1)
        self.assertEqual(
            combined.projection.link_occurrences.num_rows,
            2,
        )
        self.assertEqual(
            combined.projection.content_hashes,
            {"hash-0", "hash-1"},
        )

    def test_shared_content_retains_each_visit_and_dom_occurrence(
        self,
    ) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = """
        <html><body>
          <a href="/next">First</a>
          <a href="/next">Second</a>
        </body></html>
        """
        observed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        observations = tuple(
            DocumentObservation(
                visit_id=f"10000000-0000-0000-0000-00000000000{index}",
                document_id=f"20000000-0000-0000-0000-00000000000{index}",
                source_url="https://example.com/base",
                observed_at=observed_at,
            )
            for index in (1, 2)
        )

        projection = project_documents(
            repository,
            (
                DocumentProjectionSource(
                    content_sha256="shared",
                    object_key="objects/shared",
                    storage_encoding="zstd",
                    content_bytes=100,
                    observations=observations,
                ),
            ),
        )

        self.assertEqual(projection.links.num_rows, 1)
        self.assertEqual(projection.link_occurrences.num_rows, 4)
        rollup = projection.links.to_pylist()[0]
        self.assertEqual(rollup["visit_count"], 2)
        self.assertEqual(rollup["distinct_content_count"], 1)
        self.assertEqual(rollup["occurrence_count"], 4)
        occurrence_keys = {
            (row["document_id"], row["element_index"])
            for row in projection.link_occurrences.to_pylist()
        }
        self.assertEqual(len(occurrence_keys), 4)


if __name__ == "__main__":
    unittest.main()
