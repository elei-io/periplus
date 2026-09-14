"""Bounded execution-only scoping for a simple capture/heading inner join.

Keep the public views intact. Discovery and extraction share the caller's read
transaction and deadline; the original join preserves capture multiplicity.
"""
from dataclasses import dataclass

from sqlglot import exp

MAX_CONTENTS = 1024
MAX_KEY_BYTES = 128 * 1024
OPTIMIZATION = "capture_heading_exact_scope_v1"
_CTE = "_query_scoped_headings"


@dataclass(frozen=True)
class HeadingScope:
    statement: exp.Select
    heading: exp.Table
    selector: exp.Select

    def resolve(self, connection) -> str | None:
        rows = connection.execute(self.selector.sql(dialect="duckdb")).fetchall()
        if len(rows) > MAX_CONTENTS:
            return None
        keys = [row[0] for row in rows]
        if any(not isinstance(key, str) for key in keys):
            return None
        if sum(len(key.encode()) for key in keys) > MAX_KEY_BYTES:
            return None
        scoped = exp.select("*").from_("experimental.html_heading")
        predicate = (
            exp.Anonymous(this="list_contains", expressions=[
                exp.Array(expressions=[exp.Literal.string(key) for key in keys]),
                exp.column("content_id"),
            ]) if keys else exp.false()
        )
        scoped = scoped.where(predicate)
        result = self.statement.copy()
        table = next(table for table in result.find_all(exp.Table) if table.name == "html_heading")
        replacement = exp.to_table(_CTE)
        alias = self.heading.args.get("alias")
        replacement.set("alias", alias.copy() if alias is not None else exp.TableAlias(this=self.heading.this.copy()))
        table.replace(replacement)
        result.set("with_", exp.With(expressions=[exp.CTE(
            this=scoped, alias=exp.TableAlias(this=exp.to_identifier(_CTE)), materialized=True,
        )]))
        return result.sql(dialect="duckdb")


def heading_scope(statement: exp.Expression, parameters: list) -> HeadingScope | None:
    # Deliberately small initial grammar: no parameter re-binding, nested scopes,
    # CTE collisions, outer joins, or implicit/comma join conditions.
    if parameters or not isinstance(statement, exp.Select):
        return None
    if statement.args.get("with_") or any(statement.find_all(exp.Subquery, exp.Placeholder)):
        return None
    if any(statement.args.get(key) for key in ("group", "having", "qualify", "distinct", "sample")):
        return None
    if any(statement.find_all(exp.AggFunc, exp.Window)):
        return None
    if any(c.db or c.catalog for c in statement.find_all(exp.Column)):
        return None
    tables = list(statement.find_all(exp.Table))
    if len(tables) != 2 or {t.name for t in tables} != {"capture", "html_heading"}:
        return None
    if any(t.catalog or t.db not in ("", "experimental") for t in tables):
        return None
    if any(any(value for key, value in t.args.items() if key not in {"this", "db", "catalog", "alias"}) for t in tables):
        return None
    if any(t.args.get("alias") and t.args["alias"].args.get("columns") for t in tables):
        return None
    capture = next(t for t in tables if t.name == "capture")
    heading = next(t for t in tables if t.name == "html_heading")
    if capture.alias_or_name == heading.alias_or_name or _CTE in {t.alias_or_name for t in tables}:
        return None
    joins = statement.args.get("joins", [])
    if len(joins) != 1:
        return None
    join = joins[0]
    if join.args.get("side") or join.args.get("kind") not in (None, "", "INNER") or join.args.get("method"):
        return None
    using = join.args.get("using")
    if using:
        if [x.name for x in using] != ["content_id"]:
            return None
    else:
        condition = join.args.get("on")
        if not isinstance(condition, exp.EQ):
            return None
        cols = (condition.this, condition.expression)
        if not all(isinstance(c, exp.Column) and c.name == "content_id" for c in cols):
            return None
        if {c.table for c in cols} != {capture.alias_or_name, heading.alias_or_name}:
            return None
    where = statement.args.get("where")
    if where is None:
        return None
    predicates = []
    has_text_search = False
    for predicate in where.this.flatten() if isinstance(where.this, exp.And) else [where.this]:
        if not isinstance(predicate, (exp.EQ, exp.Like, exp.ILike)):
            continue
        col, value = predicate.this, predicate.expression
        if not isinstance(col, exp.Column) or not isinstance(value, exp.Literal) or not value.is_string:
            continue
        if isinstance(predicate, (exp.Like, exp.ILike)) and col.name == "text" and col.table in ("", heading.alias_or_name):
            has_text_search = True
        if col.name not in {"requested_url", "effective_url"} or col.table not in ("", capture.alias_or_name):
            continue
        predicates.append(predicate.copy())
    if not predicates or not has_text_search:
        return None
    alias = capture.args.get("alias")
    key = exp.Column(this=exp.to_identifier("content_id"), table=(alias.this if alias is not None else capture.this).copy())
    selector = exp.select(key.copy()).distinct().from_(capture.copy())
    selector = selector.where(exp.and_(*predicates, exp.Not(this=exp.Is(this=key, expression=exp.Null()))))
    selector = selector.limit(MAX_CONTENTS + 1)
    return HeadingScope(statement, heading, selector)
