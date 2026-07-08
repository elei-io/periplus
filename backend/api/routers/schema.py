from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from api.routers.action_runs import run_action
from actions.shared.extract_schema.schemas import Input, SchemaOutput
from db.session import get_session

router = APIRouter(prefix="/schema", tags=["schema"])
_SCHEMA_ADAPTER = TypeAdapter(SchemaOutput)


@router.post("/", response_model=SchemaOutput)
async def schema(request: Input, session: Annotated[Session, Depends(get_session)]) -> SchemaOutput:
    return await run_action(
        session=session,
        primitive="schema",
        input_value=request.model_dump(),
        response_adapter=_SCHEMA_ADAPTER,
    )
