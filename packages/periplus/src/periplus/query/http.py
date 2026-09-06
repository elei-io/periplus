"""Bounded read-only SQL access to the public Periplus catalogue."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from periplus.platform.catalogue.control import CatalogueControl, get_catalogue_control
from periplus.platform.catalogue.public import (
    PUBLIC_CATALOGUE_VERSION,
    PUBLIC_SCHEMAS,
    installed_public_objects,
    known_public_objects,
    public_objects,
)


router = APIRouter(prefix="/sql", tags=["sql"])
class SqlColumn(BaseModel):
    name: str
    data_type: str
    nullable: bool
    description: str | None


class SqlRelation(BaseModel):
    schema_name: Literal["web", "content"]
    name: str
    kind: Literal["view"]
    description: str | None
    columns: list[SqlColumn]


class SqlMacroParameter(BaseModel):
    name: str
    data_type: str


class SqlMacro(BaseModel):
    schema_name: Literal["web", "content"]
    name: str
    kind: Literal["scalar_macro", "table_macro"]
    parameters: list[SqlMacroParameter]
    return_type: str | None
    columns: list[SqlColumn]


class SqlMetadataResponse(BaseModel):
    catalogue_version: str
    duckdb_version: str
    catalogue_bytes: int = Field(ge=0)
    relations: list[SqlRelation]
    macros: list[SqlMacro]


@router.get("/metadata", response_model=SqlMetadataResponse)
async def metadata(
    control: Annotated[CatalogueControl, Depends(get_catalogue_control)],
) -> SqlMetadataResponse:
    (
        version,
        duckdb_version,
        catalogue_bytes,
        rows,
        macro_rows,
    ) = await control.run(lambda _session, catalogue: _public_metadata(catalogue))
    relations: dict[tuple[str, str], SqlRelation] = {}
    for (
        schema_name,
        table_name,
        relation_description,
        column_name,
        data_type,
        nullable,
        column_description,
    ) in rows:
        key = (str(schema_name), str(table_name))
        relation = relations.setdefault(
            key,
            SqlRelation(
                schema_name=str(schema_name),
                name=str(table_name),
                kind="view",
                description=(
                    str(relation_description)
                    if relation_description is not None
                    else None
                ),
                columns=[],
            ),
        )
        relation.columns.append(
            SqlColumn(
                name=str(column_name),
                data_type=str(data_type),
                nullable=bool(nullable),
                description=(
                    str(column_description)
                    if column_description is not None
                    else None
                ),
            )
        )
    macros = [
        SqlMacro(
            schema_name=item.schema,
            name=item.name,
            kind="table_macro" if item.kind == "table_macro" else "scalar_macro",
            parameters=[
                SqlMacroParameter(name=name, data_type=data_type)
                for name, data_type in item.parameters
            ],
            return_type=item.return_type,
            columns=[
                SqlColumn(
                    name=str(column[0]),
                    data_type=str(column[1]),
                    nullable=str(column[2]).upper() == "YES",
                    description=None,
                )
                for column in macro_rows.get((item.schema, item.name), ())
            ],
        )
        for item in known_public_objects()
        if item.kind in {"macro", "table_macro"}
        and item.exposed
        and (
            item.kind == "macro"
            or (item.schema, item.name) in macro_rows
        )
    ]
    return SqlMetadataResponse(
        catalogue_version=version,
        duckdb_version=duckdb_version,
        catalogue_bytes=catalogue_bytes,
        relations=list(relations.values()),
        macros=macros,
    )


def _public_metadata(
    catalogue,
) -> tuple[
    str,
    str,
    int,
    list[tuple],
    dict[tuple[str, str], list[tuple]],
]:
    alias = "'" + catalogue.config.alias.replace("'", "''") + "'"
    runtime_rows = catalogue.trusted_remote_rows(
        f"""
        SELECT version(),
               coalesce(
                   sum(
                       coalesce(file_size_bytes, 0)
                       + coalesce(delete_file_size_bytes, 0)
                   ),
                   0
               )::BIGINT
        FROM ducklake_table_info({alias})
        """
    )
    if len(runtime_rows) != 1:
        raise RuntimeError("catalogue runtime metadata query returned no value")
    duckdb_version, catalogue_bytes = runtime_rows[0]
    macro_rows: dict[tuple[str, str], list[tuple]] = {}
    for item in installed_public_objects(catalogue):
        if item.kind != "table_macro" or not item.exposed:
            continue
        if item.arguments_sql is None:
            raise RuntimeError(
                f"{item.schema}.{item.name} has no metadata arguments"
            )
        macro_rows[(item.schema, item.name)] = catalogue.trusted_remote_rows(
            f"DESCRIBE SELECT * FROM {item.schema}.{item.name}"
            f"({item.arguments_sql})"
        )
    return (
        PUBLIC_CATALOGUE_VERSION,
        str(duckdb_version),
        int(catalogue_bytes),
        _public_metadata_rows(catalogue),
        macro_rows,
    )


def _public_metadata_rows(catalogue) -> list[tuple]:
    rows = catalogue.trusted_remote_rows(
        """
        SELECT views.schema_name,
               views.view_name,
               views.comment,
               columns.column_name,
               columns.data_type,
               columns.is_nullable
        FROM duckdb_views() AS views
        JOIN duckdb_columns() AS columns
          ON columns.database_oid = views.database_oid
         AND columns.schema_oid = views.schema_oid
         AND columns.table_oid = views.view_oid
        WHERE views.database_name = current_catalog()
           AND views.schema_name IN ('web', 'content')
        ORDER BY views.schema_name,
                 views.view_name,
                 columns.column_index
        """
    )
    descriptions = {
        (item.schema, item.name): dict(item.column_comments)
        for item in public_objects()
        if item.kind == "view"
    }
    return [
        (
            schema_name,
            view_name,
            view_comment,
            column_name,
            data_type,
            nullable,
            descriptions[(str(schema_name), str(view_name))][
                str(column_name)
            ],
        )
        for (
            schema_name,
            view_name,
            view_comment,
            column_name,
            data_type,
            nullable,
        ) in rows
    ]
