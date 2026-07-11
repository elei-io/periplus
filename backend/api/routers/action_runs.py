from typing import Any

from fastapi import HTTPException, Response
from sqlalchemy.orm import Session

from control.tasks.schemas import TaskPrimitive
from control.tasks.service import TaskValidationError
from runtime.task_runs import (
    TaskRunConflictError,
    TaskRunSubmission,
    enqueue_ad_hoc_task_run,
)
async def submit_action(
    session: Session,
    primitive: TaskPrimitive,
    input_value: dict[str, Any],
    response: Response,
) -> TaskRunSubmission:
    try:
        submission = await enqueue_ad_hoc_task_run(
            session=session,
            primitive=primitive,
            input_value=input_value,
        )
        response.headers["Location"] = f"/task-runs/{submission.run_id}"
        return submission
    except TaskValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TaskRunConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
