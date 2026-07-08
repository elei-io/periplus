from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artifacts.models import Artifact
from urls.models import Url

from .models import Crawl
from .schemas import CrawlListRecord


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def _warning_count(crawl: Crawl) -> int:
    value = (crawl.warnings_json or {}).get("count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _error_message(crawl: Crawl) -> str | None:
    value = (crawl.errors_json or {}).get("message")
    return value if isinstance(value, str) and value else None


def list_crawls(
    session: Session,
    *,
    url_pattern: str | None = None,
    domain: str | None = None,
    success: bool | None = None,
    status_code: int | None = None,
    warnings: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[CrawlListRecord]:
    statement = select(Crawl).join(Url, Crawl.url_id == Url.id)
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if domain:
        statement = statement.where(Url.domain.ilike(f"%{domain}%"))
    if success is not None:
        statement = statement.where(Crawl.success == success)
    if status_code is not None:
        statement = statement.where(Crawl.status_code == status_code)
    if warnings is not None:
        warning_value = func.coalesce(Crawl.warnings_json["count"].as_integer(), 0)
        statement = statement.where(warning_value > 0 if warnings else warning_value == 0)

    statement = statement.order_by(Crawl.started_at.desc()).limit(limit).offset(offset)
    rows: list[CrawlListRecord] = []
    for crawl in session.scalars(statement):
        warning_count = _warning_count(crawl)
        artifact_count = session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.crawl_id == crawl.id)
        )
        rows.append(
            CrawlListRecord(
                id=crawl.id,
                url_id=crawl.url_id,
                task_run_id=crawl.task_run_id,
                started_at=crawl.started_at,
                finished_at=crawl.finished_at,
                duration_ms=crawl.duration_ms,
                input_hash=crawl.input_hash,
                success=crawl.success,
                status_code=crawl.status_code,
                retry_count=crawl.retry_count,
                warning_count=warning_count,
                error_message=_error_message(crawl),
                artifact_count=int(artifact_count or 0),
                url=crawl.url.url,
                normalized_url=crawl.url.normalized_url,
                domain=crawl.url.domain,
                path_name=crawl.url.path,
            )
        )

    return rows


def count_crawls(
    session: Session,
    *,
    url_pattern: str | None = None,
    domain: str | None = None,
    success: bool | None = None,
    status_code: int | None = None,
    warnings: bool | None = None,
) -> int:
    statement = select(func.count()).select_from(Crawl).join(Url, Crawl.url_id == Url.id)
    if url_pattern:
        statement = statement.where(Url.normalized_url.ilike(_sql_like_from_glob(url_pattern), escape="\\"))
    if domain:
        statement = statement.where(Url.domain.ilike(f"%{domain}%"))
    if success is not None:
        statement = statement.where(Crawl.success == success)
    if status_code is not None:
        statement = statement.where(Crawl.status_code == status_code)
    if warnings is not None:
        warning_value = func.coalesce(Crawl.warnings_json["count"].as_integer(), 0)
        statement = statement.where(warning_value > 0 if warnings else warning_value == 0)

    return int(session.scalar(statement) or 0)


def get_crawl(session: Session, crawl_id: UUID) -> Crawl | None:
    return session.get(Crawl, crawl_id)
