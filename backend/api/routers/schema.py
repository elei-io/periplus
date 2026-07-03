from fastapi import APIRouter

from domains.schema.models import Input, SchemaOutput
from domains.schema.service import schema as schema_service

router = APIRouter(prefix="/schema", tags=["schema"])


@router.post("/", response_model=SchemaOutput)
async def schema(request: Input) -> SchemaOutput:
    return await schema_service(**request.model_dump())
