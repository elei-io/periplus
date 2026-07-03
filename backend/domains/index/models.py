from pydantic import BaseModel


class IndexLink(BaseModel):
    source_url: str
    url: str
    text: str
    title: str
    depth: int
    link_index: int
    internal: bool
