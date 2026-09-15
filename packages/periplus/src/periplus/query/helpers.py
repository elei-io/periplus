"""Read-only helper documentation derived from the public catalogue manifest."""

from pydantic import BaseModel
from periplus.platform.clickhouse.public import PUBLIC_RELATIONS

PUBLIC_SCHEMA = "public_v1"


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
    if schema != PUBLIC_SCHEMA:
        raise ValueError("Unsupported public schema")
    descriptions = {
        "capture": "Archived HTML observations with stable capture, content and document identities.",
        "html_element": "DOM elements, attributes and text spans, identified within a document.",
        "link": "Link occurrences resolved against the captured page URL.",
        "page": "Page URLs present as captures or link destinations.",
    }
    return QueryHelpers(
        catalogue_version="public_v1",
        schema_version=schema,
        helpers=[],
        relations=[
            QueryHelper(
                name=f"public_v1.{name}",
                kind="view",
                description=description,
                parameters=[],
                columns=[],
                notes=[],
                examples=[f"SELECT * FROM public_v1.{name} LIMIT 10"],
            )
            for name, description in descriptions.items()
        ],
    )
