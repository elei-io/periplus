from typing import Literal

from pydantic import BaseModel


SearchProvider = Literal["duckduckgo", "brave", "yahoo"]


class SearchResult(BaseModel):
    url: str
    title: str
    description: str
