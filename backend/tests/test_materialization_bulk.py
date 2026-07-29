from __future__ import annotations

from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock

import pyarrow as pa

from atlas.materialization.bulk import (
    _idempotency_key,
    commit_document_projection,
    delete_material_keys,
    staged_document_projection,
)
from atlas.materialization.document_projection import (
    CONTENT_STATS_SCHEMA,
    HTML_ELEMENT_SCHEMA,
    JSONLD_SCHEMA,
    LINK_OCCURRENCE_SCHEMA,
    LINK_SCHEMA,
    DocumentProjection,
)


def _projection() -> DocumentProjection:
    observed_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    hashes = ("a" * 64, "b" * 64)
    links = (
        "10000000-0000-0000-0000-000000000001",
        "10000000-0000-0000-0000-000000000002",
    )
    source_pages = (
        "20000000-0000-0000-0000-000000000001",
        "20000000-0000-0000-0000-000000000002",
    )
    return DocumentProjection(
        content_hashes=frozenset(hashes),
        document_ids=frozenset(
            {
                "30000000-0000-0000-0000-000000000001",
                "30000000-0000-0000-0000-000000000002",
            }
        ),
        content_stats=pa.Table.from_pylist(
            [
                {
                    "content_sha256": content_hash,
                    "content_bytes": 100 + index,
                    "dom_element_count": 1,
                    "dom_max_depth": 0,
                }
                for index, content_hash in enumerate(hashes)
            ],
            schema=CONTENT_STATS_SCHEMA,
        ),
        html_elements=pa.Table.from_pylist(
            [
                {
                    "content_sha256": content_hash,
                    "element_index": 0,
                    "parent_index": None,
                    "subtree_end_index": 1,
                    "depth": 0,
                    "child_index": 0,
                    "tag": "html",
                    "namespace": "html",
                    "attributes": [],
                    "text_direct": "",
                    "text_tail": "",
                }
                for content_hash in hashes
            ],
            schema=HTML_ELEMENT_SCHEMA,
        ),
        jsonld_values=pa.Table.from_pylist(
            [
                {
                    "content_sha256": hashes[0],
                    "element_index": 0,
                    "type_terms": ["Thing"],
                    "value": '{"@type":"Thing"}',
                }
            ],
            schema=JSONLD_SCHEMA,
        ),
        links=pa.Table.from_pylist(
            [
                {
                    "link_id": link_id,
                    "source_page_id": source_page_id,
                    "target_page_id": (
                        f"40000000-0000-0000-0000-00000000000{index + 1}"
                    ),
                    "source_url": f"https://source{index}.example/",
                    "target_url": f"https://target{index}.example/",
                    "relation_scope": "external",
                    "first_seen_at": observed_at,
                    "last_seen_at": observed_at,
                    "visit_count": 1,
                    "distinct_content_count": 1,
                    "occurrence_count": 1,
                }
                for index, (link_id, source_page_id) in enumerate(
                    zip(links, source_pages, strict=True)
                )
            ],
            schema=LINK_SCHEMA,
        ),
        link_occurrences=pa.Table.from_pylist(
            [
                {
                    "occurrence_id": (
                        f"50000000-0000-0000-0000-00000000000{index + 1}"
                    ),
                    "link_id": link_id,
                    "visit_id": (
                        f"60000000-0000-0000-0000-00000000000{index + 1}"
                    ),
                    "document_id": (
                        f"30000000-0000-0000-0000-00000000000{index + 1}"
                    ),
                    "content_sha256": hashes[index],
                    "element_index": 0,
                    "raw_href": f"https://target{index}.example/",
                    "observed_at": observed_at,
                }
                for index, link_id in enumerate(links)
            ],
            schema=LINK_OCCURRENCE_SCHEMA,
        ),
    )


class MaterializationBulkTests(unittest.TestCase):
    def test_staging_is_copy_ingested_and_byte_deterministic(self) -> None:
        projection = _projection()
        targets = {
            target: f"_atlas_rebuild_{target}_run"
            for target in (
                "content_stats",
                "html_elements",
                "jsonld_values",
                "links",
                "link_occurrences",
            )
        }
        enabled = frozenset(targets)

        manifests = []
        for _ in range(2):
            with staged_document_projection(
                projection,
                targets=targets,
                enabled_targets=enabled,
            ) as files:
                manifests.append(
                    [
                        (
                            item.file_id,
                            item.table,
                            item.ingest_mode,
                            item.rows,
                            item.sha256,
                            item.md5,
                            (
                                item.partition_values[0].value
                                if item.partition_values
                                else None
                            ),
                        )
                        for item in files
                    ]
                )
                self.assertEqual(
                    sum(item.rows for item in files),
                    sum(
                        getattr(projection, target).num_rows
                        for target in targets
                    ),
                )

        self.assertEqual(manifests[0], manifests[1])
        self.assertEqual(len(manifests[0]), len(targets))
        self.assertTrue(
            all(
                item[2] == "copy" and item[-1] is None
                for item in manifests[0]
            )
        )

    def test_shadow_append_uses_replay_stable_bulk_operations(self) -> None:
        projection = _projection()
        catalogue = MagicMock()
        committed: list[tuple[str, str]] = []

        def commit(files, *, idempotency_key):
            committed.append((idempotency_key, _idempotency_key(files)))

        catalogue.commit_bulk_files.side_effect = commit
        targets = {
            target: f"_atlas_rebuild_{target}_run"
            for target in (
                "content_stats",
                "html_elements",
                "jsonld_values",
                "links",
                "link_occurrences",
            )
        }

        rows = commit_document_projection(
            catalogue,
            projection,
            targets=targets,
            enabled_targets=frozenset(targets),
        )

        self.assertEqual(rows, 9)
        catalogue.commit_bulk_files.assert_called_once()
        self.assertEqual(len(committed), 1)
        self.assertEqual(committed[0][0], committed[0][1])

    def test_public_targets_use_the_same_bulk_contract(self) -> None:
        catalogue = MagicMock()

        rows = commit_document_projection(
            catalogue,
            _projection(),
            targets={"content_stats": "content_stats"},
            enabled_targets=frozenset({"content_stats"}),
        )

        self.assertEqual(rows, 2)
        catalogue.commit_bulk_files.assert_called_once()

    def test_empty_replay_commits_no_operation(self) -> None:
        projection = _projection()
        empty = DocumentProjection(
            content_hashes=frozenset(),
            document_ids=frozenset(),
            content_stats=projection.content_stats.slice(0, 0),
            html_elements=projection.html_elements.slice(0, 0),
            jsonld_values=projection.jsonld_values.slice(0, 0),
            links=projection.links.slice(0, 0),
            link_occurrences=projection.link_occurrences.slice(0, 0),
        )
        catalogue = MagicMock()
        targets = {
            target: f"_atlas_rebuild_{target}_run"
            for target in (
                "content_stats",
                "html_elements",
                "jsonld_values",
                "links",
                "link_occurrences",
            )
        }

        rows = commit_document_projection(
            catalogue,
            empty,
            targets=targets,
            enabled_targets=frozenset(targets),
        )

        self.assertEqual(rows, 0)
        catalogue.commit_bulk_files.assert_not_called()

    def test_key_deletions_are_one_replay_stable_bulk_operation(self) -> None:
        catalogue = MagicMock()
        captured = []

        def commit(files, *, idempotency_key):
            captured.extend(files)
            self.assertEqual(idempotency_key, _idempotency_key(files))
            for item in files:
                self.assertTrue(item.path.is_file())

        catalogue.commit_bulk_files.side_effect = commit

        delete_material_keys(
            catalogue,
            {
                "content_stats": (
                    "content_stats",
                    "content_sha256",
                    "VARCHAR",
                    frozenset({"b" * 64, "a" * 64}),
                ),
                "link_occurrences": (
                    "link_occurrences",
                    "document_id",
                    "UUID",
                    frozenset(
                        {"30000000-0000-0000-0000-000000000001"}
                    ),
                ),
            },
        )

        catalogue.commit_bulk_files.assert_called_once()
        self.assertEqual(len(captured), 2)
        self.assertTrue(
            all(item.mutation_mode == "delete" for item in captured)
        )
        self.assertEqual(
            {item.match_columns for item in captured},
            {("content_sha256",), ("document_id",)},
        )
