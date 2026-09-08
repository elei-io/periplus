import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from periplus.materialization.batch import _document_sources
from periplus.materialization.document_projection import (
    DocumentObservation,
    DocumentProjectionSource,
    build_visit_batch_context,
    ducklake_varchar_bucket,
)
from periplus.materialization.registry import PROJECTIONS


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
        context = build_visit_batch_context(
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
        outputs = {
            spec.name: spec.rows(context)
            for spec in PROJECTIONS
        }
        self.assertEqual(set(outputs), {spec.name for spec in PROJECTIONS})
        for spec in PROJECTIONS:
            self.assertEqual(outputs[spec.name].schema, spec.arrow_schema)

    def test_skips_every_unowned_content_projection(self) -> None:
        repository = MagicMock()
        repository.store = SimpleNamespace()
        repository.read.return_value = (
            "<html><body><a href='/next'>Next</a></body></html>"
        )
        observed_at = datetime(2026, 1, 2, tzinfo=UTC)

        context = build_visit_batch_context(
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
        outputs = {
            spec.name: spec.rows(context)
            for spec in PROJECTIONS
        }
        for spec in PROJECTIONS:
            if spec.ownership_grain == "content":
                self.assertEqual(outputs[spec.name].num_rows, 0)

    def test_content_owner_is_stable_across_parallel_visit_batches(self) -> None:
        observed_at = datetime(2026, 1, 2, tzinfo=UTC)
        first_visit = "10000000-0000-0000-0000-000000000001"
        second_visit = "10000000-0000-0000-0000-000000000002"
        first_document = "20000000-0000-0000-0000-000000000001"
        second_document = "20000000-0000-0000-0000-000000000002"
        content_hash = "a" * 64

        class Catalogue:
            content_exists = False

            def trusted_remote_rows(self, sql):
                if "FROM material." in sql and "min(document_id::VARCHAR)" not in sql:
                    return (
                        [(content_hash,)]
                        if self.content_exists
                        else []
                    )
                if "min(document_id::VARCHAR)" in sql:
                    return [(content_hash, first_document)]
                rows = [
                    (
                        first_document,
                        first_visit,
                        content_hash,
                        "objects/a",
                        "zstd",
                        100,
                    ),
                    (
                        second_document,
                        second_visit,
                        content_hash,
                        "objects/a",
                        "zstd",
                        100,
                    ),
                ]
                return [
                    row
                    for row in rows
                    if row[0] in sql
                ]

        catalogue = Catalogue()
        run = SimpleNamespace(
            generation_tables={"html_elements": "html_elements"}
        )
        batch = SimpleNamespace(snapshot=10)
        first_rows = [
            (
                first_visit,
                first_document,
                "https://example.com/",
                observed_at,
            )
        ]
        second_rows = [
            (
                second_visit,
                second_document,
                "https://example.com/",
                observed_at,
            )
        ]

        # Resolve the later batch first to model workers racing. Ownership
        # still follows the immutable global minimum document identity.
        _sources, later_owned, _documents = _document_sources(
            catalogue,
            run,
            batch,
            second_rows,
            {
                second_visit: ("https://example.com/", observed_at),
            },
        )
        _sources, first_owned, _documents = _document_sources(
            catalogue,
            run,
            batch,
            first_rows,
            {
                first_visit: ("https://example.com/", observed_at),
            },
        )

        self.assertEqual(later_owned, frozenset())
        self.assertEqual(first_owned, frozenset({content_hash}))

        # Once the root marker commits, a future document is never allowed
        # to become a second owner even if its UUID sorts before the first.
        catalogue.content_exists = True
        _sources, repeated_owned, _documents = _document_sources(
            catalogue,
            run,
            batch,
            second_rows,
            {
                second_visit: ("https://example.com/", observed_at),
            },
        )
        self.assertEqual(repeated_owned, frozenset())

    def test_complete_rebuild_and_incremental_batches_are_logically_equal(
        self,
    ) -> None:
        html = (
            "<html><head><script type='application/ld+json'>"
            '{"@type":"Article"}</script></head>'
            "<body><a href='/next'>Next</a></body></html>"
        )
        observed_at = datetime(2026, 1, 2, tzinfo=UTC)
        observations = (
            DocumentObservation(
                visit_id="10000000-0000-0000-0000-000000000001",
                document_id="20000000-0000-0000-0000-000000000001",
                source_url="https://example.com/base",
                observed_at=observed_at,
            ),
            DocumentObservation(
                visit_id="10000000-0000-0000-0000-000000000002",
                document_id="20000000-0000-0000-0000-000000000002",
                source_url="https://example.com/base",
                observed_at=observed_at,
            ),
        )

        def context(
            selected: tuple[DocumentObservation, ...],
            *,
            owns_content: bool,
        ):
            repository = MagicMock()
            repository.store = SimpleNamespace()
            repository.read.return_value = html
            return build_visit_batch_context(
                repository,
                (
                    DocumentProjectionSource(
                        content_sha256="abc",
                        object_key="objects/abc",
                        storage_encoding="zstd",
                        content_bytes=len(html),
                        observations=selected,
                    ),
                ),
                content_output_hashes=(
                    frozenset({"abc"})
                    if owns_content
                    else frozenset()
                ),
            )

        complete = context(observations, owns_content=True)
        incremental = (
            context((observations[0],), owns_content=True),
            context((observations[1],), owns_content=False),
        )

        for spec in PROJECTIONS:
            expected = spec.rows(complete).to_pylist()
            actual = [
                row
                for batch_context in incremental
                for row in spec.rows(batch_context).to_pylist()
            ]
            key = lambda row: tuple(
                str(row[column])
                for column in spec.identity_columns
            )
            self.assertEqual(
                sorted(actual, key=key),
                sorted(expected, key=key),
                spec.name,
            )


if __name__ == "__main__":
    unittest.main()
