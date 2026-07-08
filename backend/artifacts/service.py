import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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


def is_cache_eligible(artifact: Artifact) -> bool:
    return (
        artifact.invalidated_at is None
        and warning_count(artifact) == 0
        and Path(artifact.path).is_file()
    )


def get_cached_html_artifact(
    session: Session,
    *,
    url_id: UUID,
    input_hash: str,
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
        if not is_cache_eligible(html_artifact):
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


def cleanup_artifacts(max_age_seconds: int, root: Path | None = None) -> int:
    if max_age_seconds <= 0:
        return 0

    root = root or artifacts_root()
    if not root.is_dir():
        return 0

    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in root.glob("task-runs/*"):
        if not path.is_dir():
            continue

        try:
            if path.stat().st_mtime >= cutoff:
                continue

            shutil.rmtree(path)
        except OSError:
            continue

        removed += 1

    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass

    return removed
