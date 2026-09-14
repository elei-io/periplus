"""Bound a selected capture/link join using its visit and existing source-URL sort key.

Supported shape: one CTE selecting capture_id from capture for literal page URLs,
ordered by captured_at DESC, capture_id DESC with a literal LIMIT, then one inner
capture_id join to link. Selection is deterministic and shares the request snapshot.

Each link's source_url is normalize_url(coalesce(effective_url, requested_url)) in
materialization. The selected join remains authoritative: candidate sets
only restrict the private scan, preserving duplicates, counts, filters and ordering.
The selected capture already proves the link view's EXISTS membership condition.
See docs/query-investigations/single-capture-links/ for physical evidence and limits.
"""

from dataclasses import dataclass
from importlib.resources import files
from uuid import UUID

from sqlglot import exp

from periplus.query.optimizations._catalogue import normalized_view
from periplus.query.optimizations.base import (
    OptimizationPass,
    PassContext,
    PassDecision,
)
from periplus.urls import normalize_url

MAX_CAPTURES = 128
MAX_URL_BYTES = 8192
MAX_KEY_BYTES = 128 * 1024


@dataclass(frozen=True)
class CaptureLinks:
    selection: exp.Select
    link: exp.Table


@dataclass(frozen=True)
class CaptureKey:
    capture_id: UUID
    source_url: str


def plain_table(table: exp.Expression, name: str, schema: str = "") -> bool:
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier):
        return False
    alias = table.args.get("alias")
    return (
        table.name.lower() == name.lower()
        and not table.catalog
        and table.db.lower() in ("", schema)
        and not (alias and alias.args.get("columns"))
        and not any(
            value
            for key, value in table.args.items()
            if key not in {"this", "db", "catalog", "alias"}
        )
    )


def column_is(node: exp.Expression, name: str, qualifier: str) -> bool:
    return (
        isinstance(node, exp.Column)
        and node.name.lower() == name
        and not node.db
        and not node.catalog
        and node.table.lower() in ("", qualifier.lower())
    )


def match(context: PassContext) -> CaptureLinks | None:
    statement = context.statement
    if context.parameters or not isinstance(statement, exp.Select):
        return None
    if any(
        statement.find_all(exp.Placeholder, exp.Parameter, exp.Collate, exp.Subquery)
    ):
        return None
    # No hidden nested scopes, recursive selection or renamed CTE columns.
    ctes = statement.args.get("with_")
    if not ctes or ctes.args.get("recursive") or len(ctes.expressions) != 1:
        return None
    cte = ctes.expressions[0]
    if cte.args.get("alias").args.get("columns") or cte.alias_or_name.lower() in {
        "link",
        "capture",
    }:
        return None
    selection = cte.this
    if (
        not isinstance(selection, exp.Select)
        or len(list(statement.find_all(exp.Select))) != 2
    ):
        return None
    if any(
        value
        for key, value in selection.args.items()
        if key not in {"expressions", "from_", "where", "order", "limit"}
    ):
        return None
    source = selection.args.get("from_")
    if not source or not plain_table(source.this, "capture", context.schema):
        return None
    qualifier = source.this.alias_or_name
    if len(selection.expressions) != 1 or not column_is(
        selection.expressions[0], "capture_id", qualifier
    ):
        return None
    where = selection.args.get("where")
    if not where:
        return None
    predicate = where.this
    if isinstance(predicate, exp.EQ):
        values = [predicate.expression]
    elif isinstance(predicate, exp.In) and not predicate.args.get("query"):
        values = predicate.expressions
    else:
        return None
    if (
        not column_is(predicate.this, "page_url", qualifier)
        or not values
        or not all(
            isinstance(value, exp.Literal) and value.is_string for value in values
        )
    ):
        return None
    order = selection.args.get("order")
    if not order or len(order.expressions) != 2:
        return None
    if any(
        not item.args.get("desc") or not column_is(item.this, name, qualifier)
        for item, name in zip(order.expressions, ("captured_at", "capture_id"))
    ):
        return None
    limit = selection.args.get("limit")
    if (
        not limit
        or not isinstance(limit.expression, exp.Literal)
        or not limit.expression.is_int
    ):
        return None
    if any(value for key, value in limit.args.items() if key != "expression"):
        return None
    joins = statement.args.get("joins", [])
    root_source = statement.args.get("from_")
    if not root_source or len(joins) != 1:
        return None
    join = joins[0]
    if any(
        value for key, value in join.args.items() if key not in {"this", "on", "kind"}
    ):
        return None
    if join.args.get("kind") not in (None, "", "INNER"):
        return None
    tables = (root_source.this, join.this)
    link = next(
        (table for table in tables if plain_table(table, "link", context.schema)), None
    )
    selected = next(
        (table for table in tables if plain_table(table, cte.alias_or_name)), None
    )
    if (
        link is None
        or selected is None
        or link.alias_or_name.lower() == selected.alias_or_name.lower()
    ):
        return None
    condition = join.args.get("on")
    if not isinstance(condition, exp.EQ):
        return None
    columns = (condition.this, condition.expression)
    if not all(
        isinstance(column, exp.Column)
        and column.name.lower() == "capture_id"
        and not column.db
        and not column.catalog
        for column in columns
    ):
        return None
    if {column.table.lower() for column in columns} != {
        link.alias_or_name.lower(),
        selected.alias_or_name.lower(),
    }:
        return None
    # Replacing a table with a subquery cannot preserve schema-qualified column references.
    if any(column.db or column.catalog for column in statement.find_all(exp.Column)):
        return None
    return CaptureLinks(selection, link)


def check_contract(context: PassContext) -> PassDecision | None:
    connection = context.connection
    assert connection is not None
    if connection.execute("SELECT current_setting('default_collation')").fetchone()[0]:
        return PassDecision(
            "contract_mismatch",
            "collation",
            "A non-default collation is not supported.",
        )
    installed = dict(
        connection.execute(
            "SELECT view_name, sql FROM duckdb_views() WHERE database_name=? AND schema_name=? AND view_name IN ('capture','link')",
            [context.catalogue_alias, context.schema],
        ).fetchall()
    )
    for name in ("capture", "link"):
        expected = (
            files("periplus.platform.catalogue")
            .joinpath(f"sql/{context.schema}/views/{name}.sql")
            .read_text()
        )
        if name not in installed or normalized_view(installed[name]) != normalized_view(
            expected
        ):
            return PassDecision(
                "contract_mismatch",
                "views",
                "The installed capture/link views differ from the reviewed contract.",
            )
    columns = dict(
        connection.execute(
            "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name=? AND schema_name='material' AND table_name='link_occurrences'",
            [context.catalogue_alias],
        ).fetchall()
    )
    required = {
        "visit_id": "UUID",
        "source_url": "VARCHAR",
        "element_index": "INTEGER",
        "target_url": "VARCHAR",
        "raw_href": "VARCHAR",
    }
    if any(columns.get(name) != kind for name, kind in required.items()):
        return PassDecision(
            "contract_mismatch",
            "link_columns",
            "The private link columns differ from the reviewed contract.",
        )
    return None


def lookup_keys(
    context: PassContext, matched: CaptureLinks
) -> tuple[CaptureKey, ...] | PassDecision:
    connection = context.connection
    assert connection is not None
    selection = matched.selection.copy()
    qualifier = selection.args["from_"].this.alias_or_name
    url = exp.Coalesce(
        this=exp.column("effective_url", table=qualifier),
        expressions=[exp.column("page_url", table=qualifier)],
    )
    # Bound each transferred URL as well as row count; never transfer an oversized value.
    safe_url = (
        exp.Case()
        .when(
            exp.LTE(
                this=exp.Anonymous(
                    this="octet_length",
                    expressions=[
                        exp.Anonymous(this="encode", expressions=[url.copy()])
                    ],
                ),
                expression=exp.Literal.number(MAX_URL_BYTES),
            ),
            url,
        )
        .else_(exp.Null())
    )
    selection.set("expressions", [selection.expressions[0].copy(), safe_url])
    limit = min(int(selection.args["limit"].expression.this), MAX_CAPTURES + 1)
    selection = selection.limit(limit)
    rows = connection.execute(selection.sql(dialect="duckdb")).fetchall()
    if len(rows) > MAX_CAPTURES:
        return PassDecision(
            "budget_exceeded", "captures", "The selected capture limit was exceeded."
        )
    keys = []
    size = 0
    for capture_id, source_url in rows:
        if source_url is None:
            return PassDecision(
                "budget_exceeded",
                "source_url",
                "A selected source URL is missing or exceeds its byte budget.",
            )
        try:
            key = CaptureKey(UUID(str(capture_id)), normalize_url(source_url))
        except TypeError, ValueError:
            return PassDecision(
                "contract_mismatch",
                "source_url",
                "A selected capture has an unsupported identity or source URL.",
            )
        size += 16 + len(key.source_url.encode())
        if size > MAX_KEY_BYTES:
            return PassDecision(
                "budget_exceeded",
                "key_bytes",
                "The selected key byte budget was exceeded.",
            )
        keys.append(key)
    return tuple(keys)


def rewrite(
    context: PassContext, matched: CaptureLinks, keys: tuple[CaptureKey, ...]
) -> exp.Select:
    relation = exp.Table(
        this=exp.to_identifier("link_occurrences"),
        db=exp.to_identifier("material"),
        catalog=exp.to_identifier(context.catalogue_alias, quoted=True),
    )
    scoped = exp.select(
        "visit_id AS capture_id",
        "element_index AS node_index",
        "target_url",
        "raw_href",
    ).from_(relation)
    if keys:
        ids = [
            exp.Cast(this=exp.Literal.string(str(key)), to=exp.DataType.build("UUID"))
            for key in sorted({key.capture_id for key in keys})
        ]
        urls = [
            exp.Literal.string(url) for url in sorted({key.source_url for key in keys})
        ]
        predicate = exp.and_(
            exp.column("visit_id").isin(*ids), exp.column("source_url").isin(*urls)
        )
    else:
        predicate = exp.false()
    scoped = scoped.where(predicate).subquery(alias=matched.link.alias_or_name)
    result = context.statement.copy()
    # Reuse the deterministic selection already read in this snapshot. Keep its
    # multiplicity and UUID type, including the empty case, rather than scan twice.
    selected_ids = [
        exp.Cast(
            this=exp.Literal.string(str(key.capture_id)), to=exp.DataType.build("UUID")
        )
        for key in keys
    ]
    selected_array = exp.Cast(
        this=exp.Array(expressions=selected_ids), to=exp.DataType.build("UUID[]")
    )
    frozen_selection = exp.select(
        exp.alias_(
            exp.Anonymous(this="unnest", expressions=[selected_array]), "capture_id"
        )
    )
    result.args["with_"].expressions[0].set("this", frozen_selection)
    # The matcher proves there is exactly one public link table in the statement.
    link = next(
        table for table in result.find_all(exp.Table) if table.name.lower() == "link"
    )
    link.replace(scoped)
    return result


def run(context: PassContext) -> PassDecision:
    matched = match(context)
    if matched is None:
        return PassDecision(
            "not_applicable",
            "query_shape",
            "This pass requires a bounded ordered capture selection joined to links.",
        )
    if context.connection is None:
        return PassDecision(
            "deferred",
            "capture_lookup",
            "Execution will resolve bounded capture and source URL keys.",
        )
    mismatch = check_contract(context)
    if mismatch is not None:
        return mismatch
    keys = lookup_keys(context, matched)
    if isinstance(keys, PassDecision):
        return keys
    return PassDecision(
        "applied",
        "capture_keys",
        "Restricted link reads to selected captures and source URLs.",
        statement=rewrite(context, matched, keys),
        counts={"captures": len(keys)},
    )


CAPTURE_LINKS = OptimizationPass("capture_link_scope", run)
