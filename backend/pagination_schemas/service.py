import fnmatch
import json
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PaginationSchema
from .schemas import PaginationSchemaUpdateRequest


def default_match_for_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return f"{urlunparse((parsed.scheme, parsed.netloc, path, '', '', ''))}*"


def _domain_and_path(url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(url)
    return parsed.netloc or None, parsed.path or "/"


def _identity_key(
    match: str,
    item_selector: str,
    query_param_key: str,
    query_param_value_template: str,
    start_value: int,
    value_step: int,
) -> str:
    payload = {
        "match": match,
        "item_selector": item_selector,
        "query_param_key": query_param_key,
        "query_param_value_template": query_param_value_template,
        "start_value": start_value,
        "value_step": value_step,
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def find_reusable_pagination_schema(session: Session, *, url: str) -> PaginationSchema | None:
    candidates = session.scalars(
        select(PaginationSchema)
        .where(PaginationSchema.enabled.is_(True))
        .where(PaginationSchema.validation_status != "failing")
        .order_by(PaginationSchema.priority.desc(), PaginationSchema.updated_at.desc())
    )
    for schema in candidates:
        if fnmatch.fnmatch(url, schema.match):
            return schema
    return None


def create_pagination_schema(
    session: Session,
    *,
    url: str,
    match: str,
    next_button_selector: str | None,
    item_selector: str,
    expected_max_item_count: int | None,
    query_param_key: str,
    query_param_value_template: str,
    start_value: int,
    value_step: int,
    task_run_id: UUID | None,
    generated_from_crawl_id: UUID | None,
    generated_from_artifact_id: UUID | None,
    inputs_json: dict,
    validation_status: str | None = "valid",
    warnings_json: dict | None = None,
) -> PaginationSchema:
    domain, path = _domain_and_path(url)
    schema = PaginationSchema(
        identity_key=_identity_key(
            match,
            item_selector,
            query_param_key,
            query_param_value_template,
            start_value,
            value_step,
        ),
        match=match,
        next_button_selector=next_button_selector,
        item_selector=item_selector,
        expected_max_item_count=expected_max_item_count,
        query_param_key=query_param_key,
        query_param_value_template=query_param_value_template,
        start_value=start_value,
        value_step=value_step,
        domain=domain,
        path=path,
        generated_by_task_run_id=task_run_id,
        generated_from_crawl_id=generated_from_crawl_id,
        generated_from_artifact_id=generated_from_artifact_id,
        inputs_json=inputs_json,
        validation_status=validation_status,
        warnings_json=warnings_json or {"codes": [], "count": 0, "warnings": []},
    )
    session.add(schema)
    session.flush()
    return schema


def mark_pagination_schema_failed(
    session: Session,
    schema: PaginationSchema,
    error: str,
) -> None:
    schema.failure_count += 1
    schema.last_failed_at = datetime.now(UTC)
    schema.last_error = error
    schema.validation_status = "failing"
    session.flush()


def list_pagination_schemas(session: Session) -> list[PaginationSchema]:
    return list(
        session.scalars(
            select(PaginationSchema).order_by(PaginationSchema.updated_at.desc())
        )
    )


def get_pagination_schema(session: Session, schema_id: UUID) -> PaginationSchema | None:
    return session.get(PaginationSchema, schema_id)


def update_pagination_schema(
    session: Session,
    schema: PaginationSchema,
    request: PaginationSchemaUpdateRequest,
) -> PaginationSchema:
    if request.match is not None:
        schema.match = request.match
    if request.enabled is not None:
        schema.enabled = request.enabled
    if request.priority is not None:
        schema.priority = request.priority
    if request.next_button_selector is not None:
        schema.next_button_selector = request.next_button_selector
    if request.item_selector is not None:
        schema.item_selector = request.item_selector
    if request.expected_max_item_count is not None:
        schema.expected_max_item_count = request.expected_max_item_count
    if request.query_param_key is not None:
        schema.query_param_key = request.query_param_key
    if request.query_param_value_template is not None:
        schema.query_param_value_template = request.query_param_value_template
    if request.start_value is not None:
        schema.start_value = request.start_value
    if request.value_step is not None:
        schema.value_step = request.value_step
    if request.validation_status is not None:
        schema.validation_status = request.validation_status

    schema.identity_key = _identity_key(
        schema.match,
        schema.item_selector,
        schema.query_param_key,
        schema.query_param_value_template,
        schema.start_value,
        schema.value_step,
    )
    session.flush()
    return schema
