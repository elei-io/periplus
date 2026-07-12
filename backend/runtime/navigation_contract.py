"""Dependency-neutral navigation package reference contract."""

from pydantic import BaseModel, ConfigDict


class NavigationPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    object_name: str
    sha256: str
    schema_version: int
    recipe: str
    row_count: int
    byte_size: int
