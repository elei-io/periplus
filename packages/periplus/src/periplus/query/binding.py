"""Bind all public relations in one validated statement to one publication."""

import re
import sqlglot
from sqlglot import exp
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime
from datetime import UTC, datetime
from periplus.operations.access.schemas import QueryLimits


class PublicationBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    database: str = Field(pattern=r"^(public_v1|query_[0-9a-f]{32})$")
    revision: int = Field(ge=0)
    expires_at: AwareDatetime


class ExecutionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    limits: QueryLimits
    publication: PublicationBinding


def bind_publication(sql: str, binding: PublicationBinding) -> str:
    if binding.expires_at <= datetime.now(UTC):
        raise ValueError("Publication binding expired; request a fresh query context")
    tree = sqlglot.parse_one(sql, read="clickhouse")
    for table in tree.find_all(exp.Table):
        if table.db == "public_v1":
            if not table.alias:
                table.set("alias", exp.TableAlias(this=exp.to_identifier(table.name)))
            table.set("db", exp.to_identifier(binding.database))
    for column in tree.find_all(exp.Column):
        if column.db == "public_v1":
            column.set("db", None)
    return tree.sql(dialect="clickhouse", comments=False)
