from fastapi import APIRouter

from actions.shared.extract_schema.schemas import Input, SchemaOutput
from actions.shared.extract_schema.service import schema as schema_service

router = APIRouter(prefix="/schema", tags=["schema"])


@router.post("/", response_model=SchemaOutput)
async def schema(request: Input) -> SchemaOutput:
    return await schema_service(**request.model_dump())
