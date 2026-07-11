from typing import Literal

from pydantic import BaseModel, Field


SearchProvider = Literal["duckduckgo", "brave", "yahoo"]


class SearchResult(BaseModel):
    url: str
    title: str
    description: str


class SearchOutput(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)
