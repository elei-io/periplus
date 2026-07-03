from pydantic import BaseModel


class SearchResult(BaseModel):
    url: str
    title: str
    description: str
