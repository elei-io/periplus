"""Exact-text acceleration through bounded candidates, never replacement semantics.

A literal interior ASCII word surrounded by spaces is a complete ICU page word,
even when adjacent HTML text joins the element's edge words. The original text
predicate remains authoritative. Row IDs are private and valid only in the caller's
read snapshot. See docs/query-investigations/append-only-index/ for physical evidence.
"""

from dataclasses import dataclass
from importlib.resources import files
import re
from collections.abc import Iterator

import duckdb
from sqlglot import exp, parse_one

from periplus.query.optimizations.base import (
    OptimizationPass,
    PassContext,
    PassDecision,
)

MAX_CANDIDATE_CONTENTS = 128
MAX_CANDIDATE_NODES = 16384
MAX_NODES_PER_CONTENT = 1024
_INTERIOR_WORD = re.compile(r"(?<= )[A-Za-z0-9]{3,}(?= )")


@dataclass(frozen=True)
class TextMatch:
    term: str
    qualifier: exp.Identifier
    text_length: int


@dataclass(frozen=True)
class ContentCandidates:
    content_id: str
    node_indexes: tuple[int, ...]


def conjuncts(node: exp.Expression) -> Iterator[exp.Expression]:
    if isinstance(node, exp.Paren):
        yield from conjuncts(node.this)
    elif isinstance(node, exp.And):
        yield from conjuncts(node.left)
        yield from conjuncts(node.right)
    else:
        yield node


def match(context: PassContext) -> TextMatch | PassDecision:
    """Recognize only direct element scans with a safely anchored literal equality."""
    statement = context.statement
    if context.parameters or any(statement.find_all(exp.Placeholder, exp.Parameter)):
        return PassDecision(
            "not_applicable",
            "parameters",
            "Bound parameters are not supported by this pass.",
        )
    if not isinstance(statement, exp.Select):
        return PassDecision(
            "not_applicable",
            "statement",
            "This pass requires a SELECT over one element relation.",
        )
    if (
        any(statement.args.get(key) for key in ("with_", "joins", "qualify", "windows"))
        or len(list(statement.find_all(exp.Select))) != 1
    ):
        return PassDecision(
            "not_applicable",
            "query_shape",
            "Joins, CTEs, nested queries and window clauses are not supported.",
        )
    if any(statement.find_all(exp.Collate)):
        return PassDecision(
            "not_applicable",
            "collation",
            "Explicit collations are not supported by this pass.",
        )
    source = statement.args.get("from_")
    table = source.this if source is not None else None
    if (
        not isinstance(table, exp.Table)
        or not isinstance(table.this, exp.Identifier)
        or table.name.lower() != "html_element"
        or table.catalog
        or table.db.lower() not in ("", context.schema)
    ):
        return PassDecision(
            "not_applicable",
            "source",
            "This pass requires the selected schema’s html_element relation.",
        )
    alias = table.args.get("alias")
    if (
        alias is not None
        and alias.args.get("columns")
        or any(
            value
            for key, value in table.args.items()
            if key not in {"this", "db", "catalog", "alias"}
        )
        or statement.args.get("sample")
        or any(column.db or column.catalog for column in statement.find_all(exp.Column))
    ):
        return PassDecision(
            "not_applicable",
            "source_shape",
            "Sampling, renamed columns and multi-part column qualifiers are not supported.",
        )
    where = statement.args.get("where")
    if where is None:
        return PassDecision(
            "not_applicable", "predicate", "No exact text predicate was found."
        )
    predicates = list(conjuncts(where.this))
    for predicate in predicates:
        if isinstance(predicate, (exp.EQ, exp.In)) and any(
            column.name.lower() == "content_id"
            for column in predicate.find_all(exp.Column)
        ):
            return PassDecision(
                "not_applicable",
                "content_selected",
                "An exact content selection already provides a direct access path.",
            )
    terms: list[tuple[str, int]] = []
    for predicate in predicates:
        if not isinstance(predicate, exp.EQ):
            continue
        for column, value in (
            (predicate.left, predicate.right),
            (predicate.right, predicate.left),
        ):
            if not isinstance(column, exp.Column) or column.name.lower() != "text":
                continue
            if column.table and column.table.lower() != table.alias_or_name.lower():
                continue
            if isinstance(value, exp.Literal) and value.is_string:
                terms.extend(
                    (word.group().lower(), len(value.this))
                    for word in _INTERIOR_WORD.finditer(value.this)
                )
    if not terms:
        return PassDecision(
            "not_applicable",
            "safe_word",
            "No literal text equality contains a complete interior ASCII word.",
        )
    term, length = max(terms, key=lambda item: (len(item[0]), item[0]))
    qualifier = alias.this if alias is not None else table.this
    return TextMatch(term, qualifier.copy(), length)


def canonical_elements() -> exp.Query:
    resource = files("periplus.platform.catalogue").joinpath(
        "sql/public_v1/views/html_element.sql"
    )
    return parse_one(resource.read_text(), read="duckdb").expression


def normalized_view(sql: str) -> str:
    statement = parse_one(sql, read="duckdb").expression
    for identifier in statement.find_all(exp.Identifier):
        identifier.set("quoted", False)
        identifier.set("this", identifier.this.lower())
    return statement.sql(dialect="duckdb", comments=False)


def check_contract(
    connection: duckdb.DuckDBPyConnection, context: PassContext
) -> PassDecision | None:
    """Only the reviewed element projection can use the private physical index."""
    if connection.execute("SELECT current_setting('default_collation')").fetchone()[0]:
        return PassDecision(
            "contract_mismatch",
            "default_collation",
            "The connection has a non-default collation.",
        )
    rows = connection.execute(
        "SELECT sql FROM duckdb_views() WHERE database_name=? AND schema_name=? AND view_name='html_element'",
        [context.catalogue_alias, context.schema],
    ).fetchall()
    resource = files("periplus.platform.catalogue").joinpath(
        "sql/public_v1/views/html_element.sql"
    )
    if len(rows) == 1:
        try:
            if normalized_view(rows[0][0]) == normalized_view(resource.read_text()):
                return None
        except (ValueError, AttributeError):
            pass
    return PassDecision(
        "contract_mismatch",
        "element_view",
        "The installed element view differs from the reviewed index contract.",
    )


def private_table(context: PassContext, name: str) -> exp.Table:
    return exp.Table(
        this=exp.to_identifier(name),
        db=exp.to_identifier("material"),
        catalog=exp.to_identifier(context.catalogue_alias, quoted=True),
    )


def lookup_candidates(
    connection: duckdb.DuckDBPyConnection, context: PassContext, matched: TextMatch
) -> tuple[ContentCandidates, ...] | PassDecision:
    # The sentinel row detects overflow without reading an unbounded match set.
    # Oversized per-content arrays are not transferred to Python.
    relation = private_table(context, "html_terms").sql(dialect="duckdb")
    rows = connection.execute(
        f"SELECT content_sha256, CASE WHEN len(node_indexes)<={MAX_NODES_PER_CONTENT} "
        f"THEN node_indexes ELSE NULL END FROM {relation} WHERE term=? "
        f"LIMIT {MAX_CANDIDATE_CONTENTS + 1}",
        [matched.term],
    ).fetchall()
    counts = {"candidate_contents": len(rows)}
    if len(rows) > MAX_CANDIDATE_CONTENTS:
        return PassDecision(
            "budget_exceeded",
            "candidate_contents",
            "The content candidate limit was exceeded.",
            counts=counts,
        )
    if any(nodes is None for _, nodes in rows):
        return PassDecision(
            "budget_exceeded",
            "nodes_per_content",
            "A content posting exceeds the per-content node budget.",
            counts=counts,
        )
    counts["candidate_nodes"] = sum(len(nodes) for _, nodes in rows)
    if counts["candidate_nodes"] > MAX_CANDIDATE_NODES:
        return PassDecision(
            "budget_exceeded",
            "candidate_nodes",
            "The total node candidate limit was exceeded.",
            counts=counts,
        )
    if any(
        not isinstance(content, str)
        or any(type(node) is not int or node < 0 for node in nodes)
        for content, nodes in rows
    ):
        return PassDecision(
            "contract_mismatch",
            "posting_shape",
            "The private posting shape differs from the index contract.",
        )
    return tuple(ContentCandidates(content, tuple(nodes)) for content, nodes in rows)


def literal_membership(column: str, values: list[exp.Expression]) -> exp.In:
    # NULL preserves a literal IN filter under the measured DuckDB optimizer;
    # in a WHERE predicate it never adds a match. Do not replace it with a join.
    return exp.In(this=exp.column(column), expressions=[*values, exp.Null()])


def content_ranges(statement: exp.Expression) -> Iterator[exp.Expression]:
    """Carry existing literal content ranges into the skinny identity lookup."""
    for predicate in conjuncts(statement.args["where"].this):
        if not isinstance(predicate, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
            continue
        for column, value in (
            (predicate.left, predicate.right),
            (predicate.right, predicate.left),
        ):
            if (
                isinstance(column, exp.Column)
                and column.name.lower() == "content_id"
                and isinstance(value, exp.Literal)
                and value.is_string
            ):
                copied = predicate.copy()
                for item in copied.find_all(exp.Column):
                    item.replace(exp.column("content_sha256"))
                yield copied


def resolve_rows(
    connection: duckdb.DuckDBPyConnection,
    context: PassContext,
    matched: TextMatch,
    candidates: tuple[ContentCandidates, ...],
) -> list[int] | PassDecision:
    predicates = [
        exp.and_(
            exp.EQ(
                this=exp.column("content_sha256"),
                expression=exp.Literal.string(candidate.content_id),
            ),
            literal_membership(
                "node_index",
                [exp.Literal.number(node) for node in candidate.node_indexes],
            ),
        )
        for candidate in candidates
        if candidate.node_indexes
    ]
    if not predicates:
        return []
    lookup = (
        exp.select("rowid")
        .from_(private_table(context, "html_elements"))
        .where(exp.or_(*predicates))
    )
    lookup = lookup.where(
        literal_membership(
            "content_sha256",
            [
                exp.Literal.string(content)
                for content in sorted(
                    {candidate.content_id for candidate in candidates}
                )
            ],
        ),
        append=True,
    )
    for predicate in content_ranges(context.statement):
        lookup = lookup.where(predicate, append=True)
    lookup = lookup.where(
        exp.EQ(
            this=exp.Sub(
                this=exp.column("text_end"), expression=exp.column("text_start")
            ),
            expression=exp.Literal.number(matched.text_length),
        ),
        append=True,
    ).limit(MAX_CANDIDATE_NODES + 1)
    rowids = [
        row[0] for row in connection.execute(lookup.sql(dialect="duckdb")).fetchall()
    ]
    if len(rowids) > MAX_CANDIDATE_NODES:
        return PassDecision(
            "budget_exceeded",
            "resolved_rows",
            "The physical row candidate limit was exceeded.",
        )
    if any(type(rowid) is not int for rowid in rowids):
        return PassDecision(
            "contract_mismatch",
            "row_identity",
            "The physical row identity differs from the index contract.",
        )
    return rowids


def rewrite(context: PassContext, matched: TextMatch, rowids: list[int]) -> exp.Query:
    """Replace only the scan; keep projection, predicates, multiplicity and ordering."""
    physical = canonical_elements()
    physical.args["from_"].this.set(
        "catalog", exp.to_identifier(context.catalogue_alias, quoted=True)
    )
    restriction = (
        literal_membership(
            "rowid", [exp.Literal.number(rowid) for rowid in sorted(set(rowids))]
        )
        if rowids
        else exp.false()
    )
    physical = physical.where(restriction)
    if rowids:
        # The measured row-ID IN path still scanned payloads without this range.
        physical = physical.where(
            exp.Between(
                this=exp.column("rowid"),
                low=exp.Literal.number(min(rowids)),
                high=exp.Literal.number(max(rowids)),
            ),
            append=True,
        )
    result = context.statement.copy()
    replacement = physical.subquery()
    replacement.set("alias", exp.TableAlias(this=matched.qualifier.copy()))
    result.args["from_"].this.replace(replacement)
    return result


def run(context: PassContext) -> PassDecision:
    matched = match(context)
    if isinstance(matched, PassDecision):
        return matched
    if context.connection is None:
        return PassDecision(
            "deferred",
            "candidate_lookup",
            "Execution may look up bounded word-index candidates; preparation does not read index rows.",
        )
    connection = context.connection
    mismatch = check_contract(connection, context)
    if mismatch is not None:
        return mismatch
    candidates = lookup_candidates(connection, context, matched)
    if isinstance(candidates, PassDecision):
        return candidates
    rowids = resolve_rows(connection, context, matched, candidates)
    if isinstance(rowids, PassDecision):
        return rowids
    return PassDecision(
        "applied",
        "exact_candidates",
        "Restricted the element scan to bounded candidates; the original equality is preserved.",
        statement=rewrite(context, matched, rowids),
        counts={
            "candidate_contents": len(candidates),
            "candidate_nodes": sum(
                len(candidate.node_indexes) for candidate in candidates
            ),
            "resolved_rows": len(rowids),
        },
    )


ELEMENT_TEXT = OptimizationPass("element_text_index_candidates", run)
