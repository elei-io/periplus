"""Execute one frozen NATS task-run state against Postgres-backed definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session
from config import get_int

from actions.calibrate.service import calibrate
from actions.crawl.service import crawl
from actions.extract.service import extract
from actions.index.service import index
from actions.search.service import search
from actions.shared.data_schema.service import schema
from actions.shared.progress import ProgressReporter
from tasks.context import TaskExecutionContext, task_execution_scope
from tasks.queue import TaskRunState


class TaskRunCancelled(Exception):
    pass


def _json_safe(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return json.loads(json.dumps(value, default=str))


def validate_result_size(value: object) -> None:
    limit = get_int("ATLAS_TASK_RESULT_MAX_BYTES")
    size = len(json.dumps(value, separators=(",", ":"), default=str).encode())
    if size > limit:
        raise ValueError(f"Task result is {size} bytes, exceeding ATLAS_TASK_RESULT_MAX_BYTES={limit}.")


@dataclass
class PrimitiveExecution:
    output_json: object
    response_json: object
    warnings: list[dict]


async def execute_task(
    session: Session,
    run: TaskRunState,
    progress_reporter: ProgressReporter | None = None,
) -> PrimitiveExecution:
    payload = run.input_json
    context = TaskExecutionContext(
        run_id=run.id,
        attempt=run.attempt,
        task_id=run.task_id,
        task_revision=run.task_revision,
        primitive=run.primitive,
        data_schema_id=run.data_schema_id,
        crawl_policy_snapshots_json=run.crawl_policy_snapshots_json,
    )
    with task_execution_scope(context):
        if run.primitive == "search":
            results = await search(**payload, progress_reporter=progress_reporter, session=session, task_run_id=run.id)
            response = [_json_safe(result) for result in results]
            execution = PrimitiveExecution({"results": response}, response, [])
        elif run.primitive == "index":
            output = await index(**payload, progress_reporter=progress_reporter, session=session, task_run_id=run.id)
            response = [_json_safe(link) for link in output.links]
            execution = PrimitiveExecution({"links": response}, response, [])
        elif run.primitive == "crawl":
            output = await crawl(**payload, progress_reporter=progress_reporter, session=session, task_run_id=run.id, retain_pages=True, include_links=True)
            warnings = [_json_safe(warning) for page in output.pages for warning in page.quality_warnings]
            response = _json_safe(output)
            execution = PrimitiveExecution(response, response, warnings)
        elif run.primitive == "schema":
            output = await schema(**payload, progress_reporter=progress_reporter, session=session, task_run_id=run.id)
            response = _json_safe(output)
            execution = PrimitiveExecution(response, response, [])
        elif run.primitive == "extract":
            output = await extract(**payload, progress_reporter=progress_reporter, session=session, task_run_id=run.id)
            response = _json_safe(output)
            execution = PrimitiveExecution(response, response, [_json_safe(value) for value in output.warnings])
        elif run.primitive == "calibrate":
            output = await calibrate(**payload, progress_reporter=progress_reporter, session=session, task_run_id=run.id)
            response = _json_safe(output)
            execution = PrimitiveExecution(response, response, [])
        else:
            raise ValueError(f"Unsupported task primitive: {run.primitive}")
    validate_result_size(execution.output_json)
    return execution
