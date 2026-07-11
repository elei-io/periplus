"""Low-level analytical access to the DuckLake catalogue."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from repository import repository_ingestor_from_env
from repository.ducklake.query import CatalogueQueryError, classify_select, stream_arrow_query

router = APIRouter(prefix="/catalogue", tags=["catalogue"])


class CatalogueSqlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=100_000)


@router.post("/sql", response_class=StreamingResponse)
def sql_query(request: CatalogueSqlRequest) -> StreamingResponse:
    try:
        classify_select(request.sql)
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def body():
        with repository_ingestor_from_env() as repository:
            repository.validate()
            yield from stream_arrow_query(repository.catalogue, request.sql)

    return StreamingResponse(
        body(),
        media_type="application/vnd.apache.arrow.stream",
        headers={"Content-Disposition": 'inline; filename="catalogue.arrow"'},
    )
