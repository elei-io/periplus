import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from atlas.materialization.dom import links_from_elements
from atlas.materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
    ducklake_varchar_bucket,
    project_documents,
)


class DocumentProjectionTests(unittest.TestCase):
    def test_ducklake_bucket_uses_iceberg_murmur3(self) -> None:
        self.assertEqual(
            ducklake_varchar_bucket("foo", 64),
            (0xF6A5C420 & 0x7FFFFFFF) % 64,
        )

    def test_one_parse_produces_document_and_link_outputs(self) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = (
            "<html><head><script type='application/ld+json'>"
            '{"@type":"Article"}</script></head>'
            "<body><a href='/next'>Next</a></body></html>"
        )
        projection = project_documents(
            repository,
            (
                DocumentProjectionSource(
                    content_sha256="abc",
                    object_key="objects/abc",
                    storage_encoding="zstd",
                    content_bytes=100,
                    observations=(
                        DocumentObservation(
                            visit_id="7c1f63ab-42c9-47d8-8221-54127e15c5e7",
                            document_id="cd7ea411-330c-5492-93ac-f804eb2a3859",
                            source_url="https://example.com/base",
                            observed_at=datetime(2026, 1, 2, tzinfo=UTC),
                        ),
                    ),
                ),
            ),
        )
        repository.read.assert_called_once_with("objects/abc")
        self.assertEqual(projection.content_stats.num_rows, 1)
        self.assertGreater(projection.html_elements.num_rows, 0)
        self.assertEqual(projection.jsonld_values.num_rows, 1)
        self.assertEqual(projection.links.num_rows, 1)
        self.assertEqual(projection.link_occurrences.num_rows, 1)
        self.assertEqual(
            projection.links["target_url"].to_pylist(),
            ["https://example.com/next"],
        )

    def test_skips_unowned_content_outputs_and_reuses_link_scan(self) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = (
            "<html><body><a href='/next'>Next</a></body></html>"
        )
        observed_at = datetime(2026, 1, 2, tzinfo=UTC)

        with patch(
            "atlas.materialization.document_projection.links_from_elements",
            wraps=links_from_elements,
        ) as project_links:
            projection = project_documents(
                repository,
                (
                    DocumentProjectionSource(
                        content_sha256="abc",
                        object_key="objects/abc",
                        storage_encoding="zstd",
                        content_bytes=100,
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
                            DocumentObservation(
                                visit_id=(
                                    "4c3170d9-5bea-4240-b50e-165fd29ed70c"
                                ),
                                document_id=(
                                    "0837b504-0499-5473-815f-fd3d4d7aedda"
                                ),
                                source_url="https://example.com/base",
                                observed_at=observed_at,
                            ),
                        ),
                    ),
                ),
                content_output_hashes=frozenset(),
            )

        self.assertEqual(project_links.call_count, 1)
        self.assertEqual(projection.content_stats.num_rows, 0)
        self.assertEqual(projection.html_elements.num_rows, 0)
        self.assertEqual(projection.jsonld_values.num_rows, 0)
        self.assertEqual(projection.links.num_rows, 1)
        self.assertEqual(projection.link_occurrences.num_rows, 2)


if __name__ == "__main__":
    unittest.main()
