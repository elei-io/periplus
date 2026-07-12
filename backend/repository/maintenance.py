"""Bounded DuckLake and object-repository maintenance operations."""

from __future__ import annotations

from dataclasses import dataclass

from config import get_float, get_int
from repository.service import repository_ingestor_from_env


@dataclass(frozen=True, slots=True)
class MaintenanceConfig:
    interval_seconds: float
    minimum_files: int
    maximum_input_file_bytes: int
    target_file_bytes: int
    maximum_compacted_files: int
    maximum_operation_bytes: int
    cleanup_older_than_seconds: int
    staging_grace_seconds: float

    @classmethod
    def from_env(cls) -> "MaintenanceConfig":
        value = cls(
            interval_seconds=get_float("ATLAS_REPOSITORY_COMPACTION_INTERVAL_SECONDS"),
            minimum_files=get_int("ATLAS_REPOSITORY_COMPACTION_MIN_FILES"),
            maximum_input_file_bytes=get_int("ATLAS_REPOSITORY_COMPACTION_MAX_INPUT_FILE_BYTES"),
            target_file_bytes=get_int("ATLAS_REPOSITORY_COMPACTION_TARGET_FILE_BYTES"),
            maximum_compacted_files=get_int("ATLAS_REPOSITORY_COMPACTION_MAX_OUTPUT_FILES"),
            maximum_operation_bytes=get_int("ATLAS_REPOSITORY_COMPACTION_MAX_OPERATION_BYTES"),
            cleanup_older_than_seconds=get_int("ATLAS_REPOSITORY_CLEANUP_OLD_FILES_SECONDS"),
            staging_grace_seconds=get_float("ATLAS_INGEST_STAGING_GRACE_SECONDS"),
        )
        if value.target_file_bytes <= value.maximum_input_file_bytes:
            raise ValueError("maintenance target file size must exceed the maximum input file size")
        return value


def compact(config: MaintenanceConfig) -> None:
    with repository_ingestor_from_env() as ingestor:
        ingestor.catalogue_service.compact_small_files(
            minimum_files=config.minimum_files,
            maximum_input_file_bytes=config.maximum_input_file_bytes,
            target_file_bytes=config.target_file_bytes,
            maximum_compacted_files=config.maximum_compacted_files,
            maximum_operation_bytes=config.maximum_operation_bytes,
            cleanup_older_than_seconds=config.cleanup_older_than_seconds,
        )


def cleanup_staging(config: MaintenanceConfig) -> None:
    with repository_ingestor_from_env() as ingestor:
        ingestor.cleanup_staging(older_than_seconds=config.staging_grace_seconds)
