from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UrlRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    normalized_url: str
    scheme: str
    host: str
    domain: str
    path: str
    query_fingerprint: str | None = None
