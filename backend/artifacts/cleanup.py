from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from .service import (
    ArtifactCleanupResult,
    artifact_cache_age_seconds,
    cleanup_invalidated_artifacts,
    invalidate_expired_artifacts,
)


@dataclass(frozen=True)
class ArtifactCleanupRun:
    invalidated: int
    rows_deleted: int
    files_deleted: int
    missing_files: int
    errors: int

    @property
    def changed(self) -> bool:
        return any(
            (
                self.invalidated,
                self.rows_deleted,
                self.files_deleted,
                self.missing_files,
                self.errors,
            )
        )


def _env_int(name: str, default: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default

    try:
        return int(raw)
    except ValueError:
        return default


def artifact_cleanup_interval_seconds() -> int:
    value = _env_int("ARTIFACTS_CLEANUP_INTERVAL_SECONDS", default=-1)
    if value >= 0:
        return value

    return _env_int("ARTIFACTS_CLEANUP_INTERVAL", default=0)


def artifact_cleanup_batch_size() -> int:
    return max(_env_int("ARTIFACTS_CLEANUP_BATCH_SIZE", default=100), 1)


def run_artifact_cleanup_once(
    session_factory: sessionmaker,
    *,
    max_age_seconds: int | None = None,
    limit: int | None = None,
) -> ArtifactCleanupRun:
    with session_factory() as session:
        try:
            invalidated = invalidate_expired_artifacts(
                session,
                max_age_seconds=max_age_seconds if max_age_seconds is not None else artifact_cache_age_seconds(),
            )
            cleanup: ArtifactCleanupResult = cleanup_invalidated_artifacts(
                session=session,
                limit=limit if limit is not None else artifact_cleanup_batch_size(),
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

    return ArtifactCleanupRun(
        invalidated=invalidated,
        rows_deleted=cleanup.rows_deleted,
        files_deleted=cleanup.files_deleted,
        missing_files=cleanup.missing_files,
        errors=cleanup.errors,
    )
