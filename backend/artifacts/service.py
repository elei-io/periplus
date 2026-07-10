import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Artifact

_DEFAULT_ARTIFACTS_ROOT = Path(__file__).resolve().parents[2] / ".artifacts"
BYTE_ARTIFACT_KINDS = {"html", "screenshot", "pdf", "mhtml"}


@dataclass(frozen=True)
class CachedCrawlArtifacts:
    html_artifact: Artifact
    html: str


@dataclass(frozen=True)
class ArtifactCleanupResult:
    rows_deleted: int
    files_deleted: int
    missing_files: int
    errors: int
    anomalies: tuple[dict[str, str], ...] = ()


def artifacts_root() -> Path:
    return Path(os.getenv("ARTIFACTS_ROOT", str(_DEFAULT_ARTIFACTS_ROOT))).expanduser()


def task_run_artifacts_dir(task_run_id: UUID | str, root: Path | None = None) -> Path:
    return (root or artifacts_root()) / "task-runs" / str(task_run_id)


def warning_count(artifact: Artifact) -> int:
    value = (artifact.warnings_json or {}).get("count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _warning_codes(artifact: Artifact) -> set[str]:
    codes = (artifact.warnings_json or {}).get("codes", [])
    if not isinstance(codes, list):
        return set()
    return {code for code in codes if isinstance(code, str)}


def _blocked_by_cache_rules(
    artifact: Artifact,
    cache_block_rules: dict[str, Any] | None = None,
) -> bool:
    rules = cache_block_rules or {}
    artifact_warning_codes = rules.get("artifact_warning_codes", [])
    if not isinstance(artifact_warning_codes, list):
        return False
    blocking_codes = {code for code in artifact_warning_codes if isinstance(code, str)}
    return bool(blocking_codes & _warning_codes(artifact))


def is_cache_eligible(
    artifact: Artifact,
    *,
    cache_block_rules: dict[str, Any] | None = None,
) -> bool:
    return (
        artifact.invalidated_at is None
        and Path(artifact.path).is_file()
        and not _blocked_by_cache_rules(artifact, cache_block_rules)
    )


def get_cached_html_artifact(
    session: Session,
    *,
    url_id: UUID,
    input_hash: str,
    cache_block_rules: dict[str, Any] | None = None,
) -> CachedCrawlArtifacts | None:
    html_statement = (
        select(Artifact)
        .where(
            Artifact.url_id == url_id,
            Artifact.kind == "html",
            Artifact.input_hash == input_hash,
            Artifact.invalidated_at.is_(None),
        )
        .order_by(Artifact.created_at.desc())
    )

    for html_artifact in session.scalars(html_statement):
        if not is_cache_eligible(html_artifact, cache_block_rules=cache_block_rules):
            continue

        if html_artifact.crawl is None:
            continue

        try:
            html = Path(html_artifact.path).read_text(encoding="utf-8")
        except OSError:
            continue

        return CachedCrawlArtifacts(
            html_artifact=html_artifact,
            html=html,
        )

    return None


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def list_artifacts(
    session: Session,
    *,
    url_pattern: str | None = None,
    kind: str | None = None,
    invalidated: bool | None = None,
    warnings: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Artifact]:
    from urls.models import Url

    statement = select(Artifact).outerjoin(Url, Artifact.url_id == Url.id)
    statement = statement.where(Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if kind:
        statement = statement.where(Artifact.kind == kind)
    if invalidated is True:
        statement = statement.where(Artifact.invalidated_at.is_not(None))
    if invalidated is False:
        statement = statement.where(Artifact.invalidated_at.is_(None))
    if warnings is not None:
        warning_value = func.coalesce(Artifact.warnings_json["count"].as_integer(), 0)
        statement = statement.where(warning_value > 0 if warnings else warning_value == 0)

    statement = statement.order_by(Artifact.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(statement).unique())


def count_artifacts(
    session: Session,
    *,
    url_pattern: str | None = None,
    kind: str | None = None,
    invalidated: bool | None = None,
    warnings: bool | None = None,
) -> int:
    from urls.models import Url

    statement = select(func.count()).select_from(Artifact).outerjoin(Url, Artifact.url_id == Url.id)
    statement = statement.where(Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if kind:
        statement = statement.where(Artifact.kind == kind)
    if invalidated is True:
        statement = statement.where(Artifact.invalidated_at.is_not(None))
    if invalidated is False:
        statement = statement.where(Artifact.invalidated_at.is_(None))
    if warnings is not None:
        warning_value = func.coalesce(Artifact.warnings_json["count"].as_integer(), 0)
        statement = statement.where(warning_value > 0 if warnings else warning_value == 0)

    return int(session.scalar(statement) or 0)


def get_artifact(session: Session, artifact_id: UUID) -> Artifact | None:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None or artifact.kind not in BYTE_ARTIFACT_KINDS:
        return None
    return artifact


def invalidate_artifacts(
    session: Session,
    *,
    artifact_ids: list[UUID] | None = None,
    url_ids: list[UUID] | None = None,
    url_pattern: str | None = None,
    kind: str | None = None,
    warnings: bool | None = None,
    reason: str = "manual",
) -> int:
    from urls.models import Url

    statement = (
        select(Artifact)
        .outerjoin(Url, Artifact.url_id == Url.id)
        .where(Artifact.invalidated_at.is_(None), Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
    )
    if artifact_ids:
        statement = statement.where(Artifact.id.in_(artifact_ids))
    if url_ids:
        statement = statement.where(Artifact.url_id.in_(url_ids))
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if kind:
        statement = statement.where(Artifact.kind == kind)

    now = datetime.now(UTC)
    count = 0
    for artifact in session.scalars(statement):
        if warnings is not None and (warning_count(artifact) > 0) is not warnings:
            continue
        artifact.invalidated_at = now
        artifact.invalidated_reason = reason
        count += 1

    session.flush()
    return count


def invalidate_expired_artifacts(
    session: Session,
    *,
    max_age_seconds: int | None = None,
    reason: str = "ttl",
) -> int:
    max_age_seconds = max_age_seconds if max_age_seconds is not None else artifact_cache_age_seconds()
    if max_age_seconds <= 0:
        return 0

    cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
    statement = select(Artifact).where(
        Artifact.invalidated_at.is_(None),
        Artifact.kind.in_(BYTE_ARTIFACT_KINDS),
        Artifact.created_at < cutoff,
    )
    now = datetime.now(UTC)
    count = 0
    for artifact in session.scalars(statement):
        artifact.invalidated_at = now
        artifact.invalidated_reason = reason
        count += 1

    session.flush()
    return count


def artifact_cache_age_seconds() -> int:
    raw = os.getenv("ARTIFACT_CACHE_AGE_SECONDS", "0")
    try:
        return int(raw)
    except ValueError:
        return 0


def cleanup_invalidated_artifacts(
    session: Session,
    *,
    limit: int = 100,
    root: Path | None = None,
) -> ArtifactCleanupResult:
    root = (root or artifacts_root()).resolve()
    statement = (
        select(Artifact)
        .where(Artifact.invalidated_at.is_not(None), Artifact.kind.in_(BYTE_ARTIFACT_KINDS))
        .order_by(Artifact.invalidated_at.asc(), Artifact.created_at.asc())
        .limit(limit)
    )

    rows_deleted = 0
    files_deleted = 0
    missing_files = 0
    errors = 0
    anomalies: list[dict[str, str]] = []

    def record_anomaly(artifact: Artifact, reason: str, path: Path, error: object | None = None) -> None:
        nonlocal errors
        errors += 1
        if len(anomalies) >= 10:
            return
        anomaly = {
            "artifact_id": str(artifact.id),
            "reason": reason,
            "path": str(path),
            "artifacts_root": str(root),
        }
        if error is not None:
            anomaly["error"] = str(error)
        anomalies.append(anomaly)

    for artifact in session.scalars(statement):
        from tasks.models import TaskRunArtifact

        path = Path(artifact.path)
        try:
            resolved_path = path.resolve()
        except OSError:
            resolved_path = path

        if root not in resolved_path.parents and resolved_path != root:
            record_anomaly(artifact, "outside_root", resolved_path)
            continue

        try:
            if resolved_path.exists():
                if not resolved_path.is_file():
                    record_anomaly(artifact, "not_file", resolved_path)
                    continue
                resolved_path.unlink()
                files_deleted += 1
                _remove_empty_parents(resolved_path.parent, stop_at=root)
            else:
                missing_files += 1
        except OSError as exc:
            record_anomaly(artifact, "delete_failed", resolved_path, exc)
            continue

        for usage in session.scalars(select(TaskRunArtifact).where(TaskRunArtifact.artifact_id == artifact.id)):
            session.delete(usage)
        session.delete(artifact)
        rows_deleted += 1

    session.flush()
    return ArtifactCleanupResult(
        rows_deleted=rows_deleted,
        files_deleted=files_deleted,
        missing_files=missing_files,
        errors=errors,
        anomalies=tuple(anomalies),
    )


def _remove_empty_parents(path: Path, *, stop_at: Path) -> None:
    current = path
    while current != stop_at and stop_at in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
