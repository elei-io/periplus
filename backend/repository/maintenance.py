"""Bounded DuckLake and object-repository maintenance operations."""

from __future__ import annotations

import time
from dataclasses import dataclass

from config import get_float, get_int
from observability import repository_metrics
from repository.objects.config import staging_root_from_env
from repository.catalogue.service import CompactionResult
from repository.catalogue.operations import run_with_catalogue_retry
from repository.service import cleanup_staging_files, repository_ingestor_from_env


@dataclass(frozen=True, slots=True)
class MaintenanceConfig:
    interval_seconds: float
    debounce_seconds: float
    maximum_delay_seconds: float
    retry_seconds: float
    minimum_files: int
    maximum_tables_per_pass: int
    maximum_input_file_bytes: int
    target_file_bytes: int
    maximum_compacted_files: int
    maximum_operation_bytes: int
    cleanup_older_than_seconds: int
    staging_grace_seconds: float

    @property
    def maximum_compaction_pass_bytes(self) -> int:
        """Return the effective output bound for one compacted table slice."""

        bounded_outputs = min(
            self.maximum_compacted_files,
            max(1, self.maximum_operation_bytes // self.target_file_bytes),
        )
        return bounded_outputs * self.target_file_bytes

    @classmethod
    def from_env(cls) -> "MaintenanceConfig":
        value = cls(
            interval_seconds=get_float("ATLAS_REPOSITORY_COMPACTION_INTERVAL_SECONDS"),
            debounce_seconds=get_float("ATLAS_REPOSITORY_COMPACTION_DEBOUNCE_SECONDS"),
            maximum_delay_seconds=get_float("ATLAS_REPOSITORY_COMPACTION_MAX_DELAY_SECONDS"),
            retry_seconds=get_float("ATLAS_REPOSITORY_COMPACTION_RETRY_SECONDS"),
            minimum_files=get_int("ATLAS_REPOSITORY_COMPACTION_MIN_FILES"),
            maximum_tables_per_pass=get_int(
                "ATLAS_REPOSITORY_COMPACTION_MAX_TABLES_PER_PASS"
            ),
            maximum_input_file_bytes=get_int("ATLAS_REPOSITORY_COMPACTION_MAX_INPUT_FILE_BYTES"),
            target_file_bytes=get_int("ATLAS_REPOSITORY_COMPACTION_TARGET_FILE_BYTES"),
            maximum_compacted_files=get_int("ATLAS_REPOSITORY_COMPACTION_MAX_OUTPUT_FILES"),
            maximum_operation_bytes=get_int("ATLAS_REPOSITORY_COMPACTION_MAX_OPERATION_BYTES"),
            cleanup_older_than_seconds=get_int("ATLAS_REPOSITORY_CLEANUP_OLD_FILES_SECONDS"),
            staging_grace_seconds=get_float("ATLAS_INGEST_STAGING_GRACE_SECONDS"),
        )
        if value.target_file_bytes <= value.maximum_input_file_bytes:
            raise ValueError("maintenance target file size must exceed the maximum input file size")
        if value.target_file_bytes > value.maximum_operation_bytes:
            raise ValueError(
                "maintenance target file size must not exceed the maximum operation size"
            )
        if value.maximum_compacted_files <= 0:
            raise ValueError("maintenance maximum compacted files must be greater than zero")
        if value.debounce_seconds <= 0:
            raise ValueError("maintenance debounce must be greater than zero")
        if value.maximum_delay_seconds < value.debounce_seconds:
            raise ValueError("maintenance maximum delay must not be shorter than debounce")
        if value.retry_seconds <= 0:
            raise ValueError("maintenance retry delay must be greater than zero")
        return value


def compact(config: MaintenanceConfig) -> list[CompactionResult]:
    started = time.perf_counter()
    try:
        def attempt() -> list[CompactionResult]:
            with repository_ingestor_from_env() as ingestor:
                return ingestor.catalogue_service.compact_small_files(
                    minimum_files=config.minimum_files,
                    maximum_input_file_bytes=config.maximum_input_file_bytes,
                    target_file_bytes=config.target_file_bytes,
                    maximum_compacted_files=config.maximum_compacted_files,
                    maximum_tables=config.maximum_tables_per_pass,
                    maximum_operation_bytes=config.maximum_operation_bytes,
                    cleanup_older_than_seconds=config.cleanup_older_than_seconds,
                )

        results = run_with_catalogue_retry(
            attempt,
            description="repository compaction",
        )
    except BaseException:
        repository_metrics.compaction(
            outcome="failed",
            duration_seconds=time.perf_counter() - started,
            files_processed=0,
            files_created=0,
        )
        raise
    repository_metrics.compaction(
        outcome="succeeded",
        duration_seconds=time.perf_counter() - started,
        files_processed=sum(result.files_processed for result in results),
        files_created=sum(result.files_created for result in results),
    )
    return results


def cleanup_staging(config: MaintenanceConfig) -> None:
    cleanup_staging_files(
        staging_root_from_env(),
        older_than_seconds=config.staging_grace_seconds,
    )
