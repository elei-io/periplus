import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, call

from atlas.materialization.document_projection import (
    HTML_ELEMENT_SCHEMA,
    JSONLD_SCHEMA,
    LINK_OBSERVATION_SCHEMA,
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
        self.assertEqual(projection.html_elements.schema, HTML_ELEMENT_SCHEMA)
        self.assertEqual(projection.jsonld_values.schema, JSONLD_SCHEMA)
        self.assertEqual(projection.links.schema, LINK_SCHEMA)
        self.assertEqual(
            projection.link_observations.schema,
            LINK_OBSERVATION_SCHEMA,
        )
        self.assertGreater(projection.html_elements.num_rows, 0)
        self.assertEqual(projection.jsonld_values.num_rows, 1)
        self.assertEqual(projection.links.num_rows, 1)
        self.assertEqual(projection.link_observations.num_rows, 1)
        self.assertEqual(
            projection.jsonld_values["type_terms"].to_pylist(),
            [["Article"]],
        )
        self.assertEqual(
            projection.links["target_url"].to_pylist(),
            ["https://example.com/next"],
        )
        self.assertGreater(
            sum(
                table.nbytes
                for table in (
                    projection.html_elements,
                    projection.jsonld_values,
                    projection.links,
                    projection.link_observations,
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
                "html_elements",
                "jsonld_values",
                "links",
                "link_observations",
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
            "html_elements",
            "jsonld_values",
            "links",
            "link_observations",
        ):
            self.assertEqual(
                sum(
                    getattr(partition.projection, table_name).num_rows
                    for partition in partitions
                    if partition.projection is not None
                ),
                getattr(projection, table_name).num_rows,
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
            combined.projection.link_observations.num_rows,
            2,
        )
        self.assertEqual(
            combined.projection.content_hashes,
            {"hash-0", "hash-1"},
        )


if __name__ == "__main__":
    unittest.main()
