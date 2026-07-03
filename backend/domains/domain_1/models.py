from pydantic import BaseModel


class Domain1Response(BaseModel):
    message: str
