from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from actions.shared.data_schema.schemas import Input
from api.routers.action_runs import submit_action
from db.session import get_session
from runtime.task_runs import TaskRunSubmission

router = APIRouter(prefix="/schema", tags=["schema"])


@router.post("/", response_model=TaskRunSubmission, status_code=202)
async def schema(
    request: Input,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> TaskRunSubmission:
    return await submit_action(session, "schema", request.model_dump(), response)
