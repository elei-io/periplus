"""Read-only helper documentation derived from the public catalogue manifest."""
from pydantic import BaseModel

from periplus.platform.catalogue.public import PUBLIC_CATALOGUE_VERSION, PUBLIC_SCHEMA, known_public_objects, public_objects


class HelperField(BaseModel):
    name: str
    description: str


class QueryHelper(BaseModel):
    name: str
    kind: str
    description: str
    parameters: list[HelperField]
    columns: list[HelperField]
    notes: list[str]
    examples: list[str]


class QueryHelpers(BaseModel):
    catalogue_version: str
    schema_version: str
    helpers: list[QueryHelper]
    relations: list[QueryHelper]


def query_helpers(schema: str = PUBLIC_SCHEMA) -> QueryHelpers:
    objects = [QueryHelper(
        name=f"{item.schema}.{item.name}", kind=item.kind,
        description=item.comment or "",
        parameters=[HelperField(name=name, description=kind) for name, kind in item.parameters],
        columns=[HelperField(name=name, description=description) for name, description in item.column_comments],
        notes=list(item.notes), examples=list(item.examples),
    ) for item in public_objects(schema) if item.exposed]
    return QueryHelpers(
        catalogue_version=PUBLIC_CATALOGUE_VERSION, schema_version=schema,
        helpers=[item for item in objects if item.kind in {"macro", "table_macro"}],
        relations=[item for item in objects if item.kind == "view"],
    )


def safe_helper_error(message: str) -> str | None:
    """Expose only exact, registry-owned errors, never arbitrary DuckDB details."""
    first_line = message.splitlines()[0] if message else ""
    for helper in known_public_objects():
        for error in helper.errors:
            if first_line == f"Invalid Input Error: {error}":
                return error
    return None
