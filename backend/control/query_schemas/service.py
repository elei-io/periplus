from __future__ import annotations

import json
from hashlib import sha256
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from control.data_schemas.service import default_match_for_url
from control.url_matching import resolve_url_match_for_url

from .models import QuerySchema
from .schemas import QuerySchemaListRecord


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def identity_key_for_url_match(url_match_id: UUID) -> str:
    return _json_hash({"kind": "query_schema", "url_match_id": str(url_match_id)})


def warning_count(schema: QuerySchema) -> int:
    count = schema.warnings_json.get("count") if isinstance(schema.warnings_json, dict) else None
    return int(count or 0)


def _url_parts(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    return parsed.netloc or None, parsed.path or "/"


def upsert_query_schema(
    session: Session,
    *,
    url: str,
    schema_type: str,
    schema_json: dict,
    params_json: list[dict],
    evidence_json: list[dict],
    task_run_id: UUID | None,
    crawl_id: UUID | None,
    document_id: str | None,
    inputs_json: dict,
    warnings_json: dict | None = None,
) -> QuerySchema:
    match = default_match_for_url(url)
    url_match = resolve_url_match_for_url(session, url, task_run_id=task_run_id)
    identity_key = identity_key_for_url_match(url_match.id)
    domain, path = _url_parts(match)
    schema_hash = _json_hash({"schema": schema_json, "params": params_json})
    schema = session.scalar(select(QuerySchema).where(QuerySchema.url_match_id == url_match.id))
    if schema is None:
        schema = session.scalar(select(QuerySchema).where(QuerySchema.identity_key == identity_key))
    if schema is None:
        schema = QuerySchema(
            identity_key=identity_key,
            url_match_id=url_match.id,
            match=match,
            schema_type=schema_type,
            domain=domain,
            path=path,
            schema_json=schema_json,
            params_json=params_json,
            evidence_json=evidence_json,
            schema_hash=schema_hash,
            generated_from_crawl_id=crawl_id,
            generated_from_document_id=document_id,
            generated_by_task_run_id=task_run_id,
            inputs_json=inputs_json,
            warnings_json=warnings_json or {"count": 0, "warnings": []},
        )
        session.add(schema)
    else:
        schema.identity_key = identity_key
        schema.url_match_id = url_match.id
        schema.schema_type = schema_type
        schema.domain = domain
        schema.path = path
        schema.schema_json = schema_json
        schema.params_json = params_json
        schema.evidence_json = evidence_json
        schema.schema_hash = schema_hash
        schema.generated_from_crawl_id = crawl_id
        schema.generated_from_document_id = document_id
        schema.generated_by_task_run_id = task_run_id
        schema.inputs_json = inputs_json
        schema.warnings_json = warnings_json or {"count": 0, "warnings": []}
    session.flush()
    return schema


def find_query_schema_for_url(session: Session, *, url: str) -> QuerySchema | None:
    url_match = resolve_url_match_for_url(session, url)
    return session.scalar(
        select(QuerySchema).where(
            QuerySchema.enabled.is_(True),
            QuerySchema.url_match_id == url_match.id,
        )
    )


def _filtered_statement(
    *,
    match_pattern: str | None = None,
    domain: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
) -> Select[tuple[QuerySchema]]:
    statement = select(QuerySchema)
    if match_pattern:
        statement = statement.where(QuerySchema.match.ilike(_sql_like_from_glob(match_pattern), escape="\\"))
    if domain:
        statement = statement.where(QuerySchema.domain.ilike(f"%{domain}%"))
    if schema_type:
        statement = statement.where(QuerySchema.schema_type == schema_type)
    if enabled is not None:
        statement = statement.where(QuerySchema.enabled == enabled)
    if warnings is not None:
        warning_value = func.coalesce(QuerySchema.warnings_json["count"].as_integer(), 0)
        statement = statement.where(warning_value > 0 if warnings else warning_value == 0)
    return statement


def list_query_schemas(
    session: Session,
    *,
    match_pattern: str | None = None,
    domain: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[QuerySchemaListRecord]:
    statement = (
        _filtered_statement(
            match_pattern=match_pattern,
            domain=domain,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
        )
        .order_by(QuerySchema.updated_at.desc(), QuerySchema.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows: list[QuerySchemaListRecord] = []
    for schema in session.scalars(statement):
        rows.append(
            QuerySchemaListRecord(
                id=schema.id,
                url_match_id=schema.url_match_id,
                match=schema.match,
                enabled=schema.enabled,
                priority=schema.priority,
                schema_type=schema.schema_type,
                domain=schema.domain,
                path=schema.path,
                schema_hash=schema.schema_hash,
                param_count=len(schema.params_json or []),
                evidence_count=len(schema.evidence_json or []),
                warning_count=warning_count(schema),
                generated_from_crawl_id=schema.generated_from_crawl_id,
                generated_from_document_id=schema.generated_from_document_id,
                generated_by_task_run_id=schema.generated_by_task_run_id,
                created_at=schema.created_at,
                updated_at=schema.updated_at,
            )
        )
    return rows


def count_query_schemas(
    session: Session,
    *,
    match_pattern: str | None = None,
    domain: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
) -> int:
    statement = select(func.count()).select_from(
        _filtered_statement(
            match_pattern=match_pattern,
            domain=domain,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
        ).subquery()
    )
    return int(session.scalar(statement) or 0)


def get_query_schema(session: Session, schema_id: UUID) -> QuerySchema | None:
    return session.get(QuerySchema, schema_id)


def update_query_schema(
    session: Session,
    *,
    schema: QuerySchema,
    enabled: bool | None = None,
    priority: int | None = None,
) -> QuerySchema:
    if enabled is not None:
        schema.enabled = enabled
    if priority is not None:
        schema.priority = priority
    schema.updated_at = datetime.now(UTC)
    session.flush()
    return schema


def delete_query_schema(session: Session, *, schema: QuerySchema) -> None:
    session.delete(schema)
    session.flush()
