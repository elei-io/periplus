import json
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from hashlib import sha256
from urllib.parse import parse_qsl, urlparse, urlunparse
from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


from .models import DataSchema
from .schemas import DataSchemaListRecord, DataSchemaSummary, DataSchemaUpdateRequest


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _text_hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _target_json_hash(target_json_example: str | None) -> str | None:
    return _text_hash(target_json_example) if target_json_example else None


def default_match_for_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def match_targets_for_url(url: str) -> tuple[str, ...]:
    parsed = urlparse(url)
    path = parsed.path or "/"
    without_fragment = urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, ""))
    without_query = urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    if without_fragment == without_query:
        return (without_query,)
    return (without_fragment, without_query)


def data_schema_matches_url(schema: DataSchema, url: str) -> bool:
    return any(fnmatchcase(target, schema.match) for target in match_targets_for_url(url)) or _query_key_match(
        pattern=schema.match,
        url=url,
    )


def _query_key_match(*, pattern: str, url: str) -> bool:
    parsed_pattern = urlparse(pattern)
    parsed_url = urlparse(url)
    if not parsed_pattern.query or not parsed_pattern.query.endswith("*"):
        return False
    if (
        parsed_pattern.scheme.lower() != parsed_url.scheme.lower()
        or parsed_pattern.netloc.lower() != parsed_url.netloc.lower()
        or (parsed_pattern.path or "/") != (parsed_url.path or "/")
    ):
        return False

    key_prefix = parsed_pattern.query.removesuffix("*")
    if not key_prefix or any(char in key_prefix for char in "=&"):
        return False

    return any(key.startswith(key_prefix) for key, _value in parse_qsl(parsed_url.query, keep_blank_values=True))


def _match_specificity(pattern: str) -> tuple[int, int]:
    wildcard_count = pattern.count("*") + pattern.count("?")
    literal_count = len(pattern) - wildcard_count
    return (literal_count, -wildcard_count)


def identity_key_for(
    *,
    prompt: str,
    schema_type: str,
    target_json_example: str | None,
    match: str,
) -> str:
    return identity_key_for_hash(
        prompt=prompt,
        schema_type=schema_type,
        target_json_hash=_target_json_hash(target_json_example),
        match=match,
    )


def identity_key_for_hash(
    *,
    prompt: str,
    schema_type: str,
    target_json_hash: str | None,
    match: str,
) -> str:
    return _json_hash(
        {
            "prompt": prompt,
            "schema_type": schema_type,
            "target_json_hash": target_json_hash,
            "match": match,
        }
    )


def find_reusable_data_schema(
    session: Session,
    *,
    url: str,
    prompt: str,
    schema_type: str,
    target_json_example: str | None,
) -> DataSchema | None:
    statement = select(DataSchema).where(
        DataSchema.enabled.is_(True),
        DataSchema.prompt_hash == _text_hash(prompt),
        DataSchema.schema_type == schema_type,
    )
    target_json_hash = _target_json_hash(target_json_example)
    if target_json_hash is not None:
        statement = statement.where(DataSchema.target_json_hash == target_json_hash)

    candidates = [schema for schema in session.scalars(statement) if data_schema_matches_url(schema, url)]
    if not candidates:
        return None

    return max(
        candidates,
        key=lambda schema: (
            schema.priority,
            *_match_specificity(schema.match),
            schema.updated_at,
            schema.created_at,
        ),
    )


def create_data_schema(
    session: Session,
    *,
    url: str,
    prompt: str,
    schema_type: str,
    target_json_example: str | None,
    match: str,
    schema_json: dict,
    task_run_id: UUID | None = None,
    crawl_id: UUID | None = None,
    document_id: str | None = None,
    inputs_json: dict | None = None,
) -> DataSchema:
    parsed = urlparse(url)
    identity_key = identity_key_for(
        prompt=prompt,
        schema_type=schema_type,
        target_json_example=target_json_example,
        match=match,
    )
    existing = session.scalar(select(DataSchema).where(DataSchema.identity_key == identity_key))
    if existing is not None:
        return _update_generated_data_schema(
            session,
            schema=existing,
            identity_key=identity_key,
            match=match,
            prompt=prompt,
            schema_type=schema_type,
            target_json_example=target_json_example,
            schema_json=schema_json,
            task_run_id=task_run_id,
            crawl_id=crawl_id,
            document_id=document_id,
            inputs_json=inputs_json,
            domain=parsed.netloc or None,
            path=parsed.path or None,
        )

    schema = DataSchema(
        identity_key=identity_key,
        match=match,
        enabled=True,
        priority=0,
        prompt=prompt,
        prompt_hash=_text_hash(prompt),
        schema_type=schema_type,
        target_json_hash=_target_json_hash(target_json_example),
        domain=parsed.netloc or None,
        path=parsed.path or None,
        schema_json=schema_json,
        schema_hash=_json_hash(schema_json),
        generated_by_task_run_id=task_run_id,
        generated_from_crawl_id=crawl_id,
        generated_from_document_id=document_id,
        inputs_json=inputs_json or {},
        validation_status="generated",
        warnings_json={"codes": [], "count": 0, "warnings": []},
    )
    try:
        with session.begin_nested():
            session.add(schema)
            session.flush()
        return schema
    except IntegrityError:
        existing = session.scalar(select(DataSchema).where(DataSchema.identity_key == identity_key))
        if existing is None:
            raise
        return _update_generated_data_schema(
            session,
            schema=existing,
            identity_key=identity_key,
            match=match,
            prompt=prompt,
            schema_type=schema_type,
            target_json_example=target_json_example,
            schema_json=schema_json,
            task_run_id=task_run_id,
            crawl_id=crawl_id,
            document_id=document_id,
            inputs_json=inputs_json,
            domain=parsed.netloc or None,
            path=parsed.path or None,
        )


def _update_generated_data_schema(
    session: Session,
    *,
    schema: DataSchema,
    identity_key: str,
    match: str,
    prompt: str,
    schema_type: str,
    target_json_example: str | None,
    schema_json: dict,
    task_run_id: UUID | None,
    crawl_id: UUID | None,
    document_id: str | None,
    inputs_json: dict | None,
    domain: str | None,
    path: str | None,
) -> DataSchema:
    now = datetime.now(UTC)
    schema.identity_key = identity_key
    schema.match = match
    schema.enabled = True
    schema.prompt = prompt
    schema.prompt_hash = _text_hash(prompt)
    schema.schema_type = schema_type
    schema.target_json_hash = _target_json_hash(target_json_example)
    schema.domain = domain
    schema.path = path
    schema.schema_json = schema_json
    schema.schema_hash = _json_hash(schema_json)
    schema.generated_by_task_run_id = task_run_id
    schema.generated_from_crawl_id = crawl_id
    schema.generated_from_document_id = document_id
    schema.inputs_json = {**(inputs_json or {}), "match": match}
    schema.validation_status = "generated"
    schema.failure_count = 0
    schema.last_failed_at = None
    schema.last_error = None
    schema.warnings_json = {"codes": [], "count": 0, "warnings": []}
    schema.updated_at = now
    session.flush()
    return schema


def replace_data_schema(
    session: Session,
    *,
    schema_id: UUID,
    prompt: str,
    schema_type: str,
    target_json_example: str | None,
    schema_json: dict,
    task_run_id: UUID | None = None,
    crawl_id: UUID | None = None,
    document_id: str | None = None,
    inputs_json: dict | None = None,
    match: str | None = None,
) -> DataSchema:
    schema = session.get(DataSchema, schema_id)
    if schema is None:
        raise ValueError(f"Data schema not found: {schema_id}")

    match_value = match or schema.match
    now = datetime.now(UTC)
    schema.identity_key = identity_key_for(
        prompt=prompt,
        schema_type=schema_type,
        target_json_example=target_json_example,
        match=match_value,
    )
    schema.match = match_value
    schema.enabled = True
    schema.prompt = prompt
    schema.prompt_hash = _text_hash(prompt)
    schema.schema_type = schema_type
    schema.target_json_hash = _target_json_hash(target_json_example)
    schema.schema_json = schema_json
    schema.schema_hash = _json_hash(schema_json)
    schema.generated_by_task_run_id = task_run_id
    schema.generated_from_crawl_id = crawl_id
    schema.generated_from_document_id = document_id
    schema.inputs_json = {**(inputs_json or {}), "match": match_value}
    schema.validation_status = "generated"
    schema.failure_count = 0
    schema.last_failed_at = None
    schema.last_error = None
    schema.warnings_json = {"codes": [], "count": 0, "warnings": []}
    schema.updated_at = now
    session.flush()
    return schema


def record_data_schema_failure(
    session: Session,
    *,
    schema_id: UUID,
    error: str,
    exhausted: bool = False,
) -> DataSchema | None:
    schema = session.get(DataSchema, schema_id)
    if schema is None:
        return None

    now = datetime.now(UTC)
    schema.failure_count += 1
    schema.last_failed_at = now
    schema.last_error = error
    schema.validation_status = "failed" if exhausted else "retrying"
    schema.updated_at = now
    session.flush()
    return schema


def record_data_schema_use(session: Session, *, task_run_id: UUID | None, schema: DataSchema) -> None:
    # Run lifecycle rows are control-plane state and are finalized only by the executor.
    # Schema provenance is already recorded by generated_by_task_run_id and URL matches.
    schema.updated_at = datetime.now(UTC)
    session.flush()


def _sql_like_from_glob(pattern: str) -> str:
    return pattern.replace("%", r"\%").replace("_", r"\_").replace("*", "%")


def warning_count(schema: DataSchema) -> int:
    value = (schema.warnings_json or {}).get("count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def task_run_count(session: Session, schema_id: UUID) -> int:
    return 0


def _filtered_statement(
    *,
    match_pattern: str | None = None,
    prompt: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
) -> Select[tuple[DataSchema]]:
    statement = select(DataSchema)
    if match_pattern:
        statement = statement.where(DataSchema.match.ilike(_sql_like_from_glob(match_pattern), escape="\\"))
    if prompt:
        statement = statement.where(DataSchema.prompt.ilike(f"%{prompt}%"))
    if schema_type:
        statement = statement.where(DataSchema.schema_type == schema_type)
    if enabled is not None:
        statement = statement.where(DataSchema.enabled == enabled)
    if warnings is not None:
        warning_value = func.coalesce(DataSchema.warnings_json["count"].as_integer(), 0)
        statement = statement.where(warning_value > 0 if warnings else warning_value == 0)

    return statement


def list_data_schemas(
    session: Session,
    *,
    match_pattern: str | None = None,
    prompt: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DataSchemaListRecord]:
    statement = (
        _filtered_statement(
            match_pattern=match_pattern,
            prompt=prompt,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
        )
        .order_by(DataSchema.updated_at.desc(), DataSchema.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    rows: list[DataSchemaListRecord] = []
    for schema in session.scalars(statement):
        rows.append(
            DataSchemaListRecord(
                id=schema.id,
                match=schema.match,
                enabled=schema.enabled,
                priority=schema.priority,
                prompt=schema.prompt,
                prompt_hash=schema.prompt_hash,
                schema_type=schema.schema_type,
                target_json_hash=schema.target_json_hash,
                domain=schema.domain,
                path=schema.path,
                schema_hash=schema.schema_hash,
                validation_status=schema.validation_status,
                failure_count=schema.failure_count,
                last_failed_at=schema.last_failed_at,
                last_error=schema.last_error,
                task_run_count=task_run_count(session, schema.id),
                warning_count=warning_count(schema),
                created_at=schema.created_at,
                updated_at=schema.updated_at,
            )
        )

    return rows


def count_data_schemas(
    session: Session,
    *,
    match_pattern: str | None = None,
    prompt: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
) -> int:
    subquery = _filtered_statement(
        match_pattern=match_pattern,
        prompt=prompt,
        schema_type=schema_type,
        enabled=enabled,
        warnings=warnings,
    ).subquery()
    return int(session.scalar(select(func.count()).select_from(subquery)) or 0)


def data_schema_summary(
    session: Session,
    *,
    match_pattern: str | None = None,
    prompt: str | None = None,
    schema_type: str | None = None,
    enabled: bool | None = None,
    warnings: bool | None = None,
) -> DataSchemaSummary:
    schemas = list(
        session.scalars(
            _filtered_statement(
                match_pattern=match_pattern,
                prompt=prompt,
                schema_type=schema_type,
                enabled=enabled,
                warnings=warnings,
            )
        )
    )
    use_counts = [task_run_count(session, schema.id) for schema in schemas]
    total_schema_uses = sum(use_counts)
    used_schemas = sum(1 for count in use_counts if count > 0)
    reused_schema_uses = sum(max(count - 1, 0) for count in use_counts)
    failing_schemas = sum(
        1
        for schema in schemas
        if schema.failure_count > 0 or warning_count(schema) > 0
    )

    return DataSchemaSummary(
        total_schemas=len(schemas),
        enabled_schemas=sum(1 for schema in schemas if schema.enabled),
        used_schemas=used_schemas,
        total_schema_uses=total_schema_uses,
        reused_schema_uses=reused_schema_uses,
        reuse_rate=(reused_schema_uses / total_schema_uses) if total_schema_uses else 0,
        avg_uses_per_used_schema=(total_schema_uses / used_schemas) if used_schemas else 0,
        failing_schemas=failing_schemas,
    )


def get_data_schema(session: Session, schema_id: UUID) -> DataSchema | None:
    return session.get(DataSchema, schema_id)


def update_data_schema(
    session: Session,
    *,
    schema: DataSchema,
    request: DataSchemaUpdateRequest,
) -> DataSchema:
    if request.match is not None:
        match = request.match.strip()
        if not match:
            raise ValueError("Data schema match cannot be empty.")

        identity_key = identity_key_for_hash(
            prompt=schema.prompt,
            schema_type=schema.schema_type,
            target_json_hash=schema.target_json_hash,
            match=match,
        )
        existing = session.scalar(select(DataSchema).where(DataSchema.identity_key == identity_key))
        if existing is not None and existing.id != schema.id:
            raise ValueError("Another data schema already uses this identity.")

        schema.match = match
        schema.identity_key = identity_key

    if request.enabled is not None:
        schema.enabled = request.enabled

    if request.priority is not None:
        schema.priority = request.priority

    if request.extraction_schema is not None:
        schema.schema_json = request.extraction_schema
        schema.schema_hash = _json_hash(request.extraction_schema)
        schema.validation_status = "manual"

    if "validation_status" in request.model_fields_set:
        schema.validation_status = request.validation_status

    schema.updated_at = datetime.now(UTC)
    session.flush()
    return schema


def delete_data_schema(session: Session, *, schema: DataSchema) -> None:
    session.delete(schema)
    session.flush()
