from typing import Any

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from actions.shared.progress import CrawlProgressCallback
from tasks.schemas import TaskPrimitive
from tasks.service import (
    TaskRunConflictError,
    TaskValidationError,
    execute_ad_hoc_task_run,
)


async def run_action(
    session: Session,
    primitive: TaskPrimitive,
    input_value: dict[str, Any],
    response_adapter: TypeAdapter,
    progress_callback: CrawlProgressCallback | None = None,
) -> Any:
    try:
        response = await execute_ad_hoc_task_run(
            session=session,
            primitive=primitive,
            input_value=input_value,
            progress_callback=progress_callback,
        )
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        return response_adapter.validate_python(response)
    except ValidationError as exc:
        raise HTTPException(status_code=500, detail="Task run returned an invalid response.") from exc
