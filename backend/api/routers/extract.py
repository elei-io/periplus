from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from actions.extract.schemas import Input
from api.routers.action_runs import submit_action
from db.session import get_session
from tasks.schemas import TaskRunSubmission

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("/", response_model=TaskRunSubmission, status_code=202)
async def extract(
    request: Input,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> TaskRunSubmission:
    return await submit_action(session, "extract", request.model_dump(), response)
