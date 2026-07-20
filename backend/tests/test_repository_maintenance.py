from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from repository.catalogue.service import CompactionResult
from repository.maintenance import MaintenanceConfig, compact


def config() -> MaintenanceConfig:
    return MaintenanceConfig(
        interval_seconds=300,
        debounce_seconds=15,
        maximum_delay_seconds=120,
        retry_seconds=5,
        minimum_files=64,
        maximum_tables_per_pass=1,
        maximum_input_file_bytes=1024 * 1024,
        target_file_bytes=32 * 1024 * 1024,
        maximum_compacted_files=4,
        maximum_operation_bytes=256 * 1024 * 1024,
        cleanup_older_than_seconds=7 * 24 * 60 * 60,
        staging_grace_seconds=3600,
    )


class RepositoryMaintenanceTests(unittest.TestCase):
    def test_compaction_records_duration_and_file_counts(self) -> None:
        ingestor = MagicMock()
        ingestor.catalogue_service.compact_small_files.return_value = [
            CompactionResult(
                schema_name="main",
                table_name="crawls",
                eligible_files=70,
                eligible_bytes=7000,
                files_processed=68,
                files_created=2,
            ),
            CompactionResult(
                schema_name="main",
                table_name="elements",
                eligible_files=130,
                eligible_bytes=13000,
                files_processed=128,
                files_created=4,
            ),
        ]

        with (
            patch(
                "repository.maintenance.repository_ingestor_from_env"
            ) as ingestor_factory,
            patch(
                "repository.maintenance.repository_metrics.compaction"
            ) as compaction_metric,
            patch(
                "repository.maintenance.time.perf_counter",
                side_effect=[10.0, 12.5],
            ),
        ):
            ingestor_factory.return_value.__enter__.return_value = ingestor
            result = compact(config())

        compaction_metric.assert_called_once_with(
            outcome="succeeded",
            duration_seconds=2.5,
            files_processed=196,
            files_created=6,
        )
        self.assertEqual(result, ingestor.catalogue_service.compact_small_files.return_value)

    def test_failed_compaction_records_the_failed_pass(self) -> None:
        ingestor = MagicMock()
        ingestor.catalogue_service.compact_small_files.side_effect = RuntimeError(
            "compaction failed"
        )

        with (
            patch(
                "repository.maintenance.repository_ingestor_from_env"
            ) as ingestor_factory,
            patch(
                "repository.maintenance.repository_metrics.compaction"
            ) as compaction_metric,
            patch(
                "repository.maintenance.time.perf_counter",
                side_effect=[20.0, 20.25],
            ),
        ):
            ingestor_factory.return_value.__enter__.return_value = ingestor
            with self.assertRaisesRegex(RuntimeError, "compaction failed"):
                compact(config())

        compaction_metric.assert_called_once_with(
            outcome="failed",
            duration_seconds=0.25,
            files_processed=0,
            files_created=0,
        )


if __name__ == "__main__":
    unittest.main()
