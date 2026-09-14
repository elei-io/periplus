"""Conservative candidate lookup for exact, stored element text.

An interior ASCII word surrounded by literal spaces is a complete ICU page word
regardless of adjacent HTML text. Edge words have no such guarantee. Candidate
element identities are resolved to native row IDs within the same read snapshot; the original exact text predicate remains authoritative.
"""
from dataclasses import dataclass
from importlib.resources import files
import re

import duckdb
from sqlglot import exp, parse_one

MAX_CANDIDATE_CONTENTS = 128
MAX_CANDIDATE_NODES = 16384
MAX_NODES_PER_CONTENT = 1024
OPTIMIZATION = "element_text_index_candidates"
_INTERIOR_WORD = re.compile(r"(?<= )[A-Za-z0-9]{3,}(?= )")


@dataclass(frozen=True)
class TextAnchor:
    term: str
    content_column: exp.Column
    text_length: int


@dataclass(frozen=True)
class TextIndexRewrite:
    sql: str
    candidate_count: int


def exact_text_anchor(statement: exp.Expression, parameters=()) -> TextAnchor | None:
    """Recognize only a single direct element relation and conjunctive equality."""
    if parameters or not isinstance(statement, exp.Select):
        return None
    if any(statement.args.get(key) for key in ("with_", "joins", "qualify", "windows")):
        return None
    if len(list(statement.find_all(exp.Select))) != 1:
        return None
    if any(statement.find_all(exp.Placeholder, exp.Parameter, exp.Collate)):
        return None
    source = statement.args.get("from_")
    table = source.this if source is not None else None
    if not isinstance(table, exp.Table) or not isinstance(table.this, exp.Identifier):
        return None
    if table.name.lower() != "html_element" or table.catalog or table.db.lower() not in ("", "experimental"):
        return None
    alias = table.args.get("alias")
    if alias is not None and alias.args.get("columns"):
        return None
    if (any(value for key, value in table.args.items() if key not in {"this", "db", "catalog", "alias"})
            or statement.args.get("sample")
            or any(column.db or column.catalog for column in statement.find_all(exp.Column))):
        return None
    where = statement.args.get("where")
    if where is None:
        return None

    def conjuncts(node):
        if isinstance(node, exp.Paren):
            yield from conjuncts(node.this)
        elif isinstance(node, exp.And):
            yield from conjuncts(node.left)
            yield from conjuncts(node.right)
        else:
            yield node

    predicates = list(conjuncts(where.this))
    # Exact selected-content queries already have their direct access path.
    for predicate in predicates:
        if isinstance(predicate, (exp.EQ, exp.In)) and any(
            column.name.lower() == "content_id" for column in predicate.find_all(exp.Column)
        ):
            return None
    terms = []
    for predicate in predicates:
        if not isinstance(predicate, exp.EQ):
            continue
        for column, value in ((predicate.left, predicate.right), (predicate.right, predicate.left)):
            if not isinstance(column, exp.Column) or column.name.lower() != "text":
                continue
            if column.catalog or column.db or (column.table and column.table.lower() != table.alias_or_name.lower()):
                continue
            if isinstance(value, exp.Literal) and value.is_string:
                terms.extend((match.group().lower(), len(value.this)) for match in _INTERIOR_WORD.finditer(value.this))
    if not terms:
        return None
    qualifier = alias.this.copy() if alias is not None else table.this.copy()
    term, length = max(terms, key=lambda item: (len(item[0]), item[0]))
    return TextAnchor(term, exp.Column(this=exp.to_identifier("content_id"), table=qualifier), length)


def _view_select(sql: str) -> str:
    statement = parse_one(sql, read="duckdb")
    select = statement.expression
    for identifier in select.find_all(exp.Identifier):
        identifier.set("quoted", False)
        identifier.set("this", identifier.this.lower())
    return select.sql(dialect="duckdb", comments=False)


def _index_contract_matches(connection: duckdb.DuckDBPyConnection, alias: str) -> bool:
    if connection.execute("SELECT current_setting('default_collation')").fetchone()[0]:
        return False
    installed = dict(connection.execute(
        "SELECT view_name, sql FROM duckdb_views() WHERE database_name=? "
        "AND schema_name='experimental' AND view_name='html_element'", [alias]).fetchall())
    # The primitive view must still match the reviewed element shape. Postings
    # are read directly from the private, startup-validated physical relation;
    # there is no independently mutable public posting view to inspect.
    expected = files("periplus.platform.catalogue").joinpath("sql/public_v1/views/html_element.sql")
    if "html_element" not in installed:
        return False
    try:
        return _view_select(installed["html_element"]) == _view_select(expected.read_text())
    except (ValueError, AttributeError):
        return False


def text_index_rewrite(connection: duckdb.DuckDBPyConnection, statement: exp.Expression,
                       alias: str, parameters=()) -> TextIndexRewrite | None:
    """Call only inside the request's read transaction and execution deadline."""
    anchor = exact_text_anchor(statement, parameters)
    if anchor is None or not _index_contract_matches(connection, alias):
        return None
    quoted_alias = '"' + alias.replace('"', '""') + '"'
    rows = connection.execute(
        f"SELECT content_sha256 AS content_id, CASE WHEN len(node_indexes)<={MAX_NODES_PER_CONTENT} "
        f"THEN node_indexes ELSE NULL END FROM {quoted_alias}.material.html_terms WHERE term=? "
        f"LIMIT {MAX_CANDIDATE_CONTENTS + 1}", [anchor.term]).fetchall()
    if (len(rows) > MAX_CANDIDATE_CONTENTS
            or any(not isinstance(row[0], str) or row[1] is None for row in rows)
            or sum(len(row[1]) for row in rows) > MAX_CANDIDATE_NODES):
        return None
    predicates = []
    for content, nodes in rows:
        if any(not isinstance(node, int) or node < 0 for node in nodes):
            return None
        if nodes:
            predicates.append(exp.and_(
                exp.EQ(this=exp.column("content_sha256"), expression=exp.Literal.string(content)),
                exp.In(this=exp.column("node_index"),
                       expressions=[*[exp.Literal.number(node) for node in nodes], exp.Null()])))
    rowids = []
    if predicates:
        lookup = exp.select("rowid").from_(exp.Table(
            this=exp.to_identifier("html_elements"), db=exp.to_identifier("material"),
            catalog=exp.to_identifier(alias, quoted=True))).where(exp.or_(*predicates)).limit(MAX_CANDIDATE_NODES + 1)
        lookup = lookup.where(exp.In(this=exp.column("content_sha256"), expressions=[
            *[exp.Literal.string(content) for content in sorted({row[0] for row in rows})], exp.Null()]), append=True)
        # Preserve safe literal content ranges in the skinny identity lookup too.
        def content_ranges(node):
            if isinstance(node, exp.Paren):
                yield from content_ranges(node.this)
            elif isinstance(node, exp.And):
                yield from content_ranges(node.left)
                yield from content_ranges(node.right)
            elif isinstance(node, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
                for column, value in ((node.left, node.right), (node.right, node.left)):
                    if (isinstance(column, exp.Column) and column.name.lower() == "content_id"
                            and isinstance(value, exp.Literal) and value.is_string):
                        predicate = node.copy()
                        for item in predicate.find_all(exp.Column):
                            item.replace(exp.column("content_sha256"))
                        yield predicate
        for predicate in content_ranges(statement.args["where"].this):
            lookup = lookup.where(predicate, append=True)
        lookup = lookup.where(exp.EQ(this=exp.Sub(this=exp.column("text_end"), expression=exp.column("text_start")),
                                     expression=exp.Literal.number(anchor.text_length)), append=True)
        rowids = [row[0] for row in connection.execute(lookup.sql(dialect="duckdb")).fetchall()]
        if len(rowids) > MAX_CANDIDATE_NODES or any(not isinstance(rowid, int) for rowid in rowids):
            return None
    root = files("periplus.platform.catalogue").joinpath("sql/public_v1/views/html_element.sql")
    physical = parse_one(root.read_text(), read="duckdb").expression
    physical.args["from_"].this.set("catalog", exp.to_identifier(alias, quoted=True))
    restriction = (exp.In(this=exp.column("rowid"), expressions=[
        *[exp.Literal.number(rowid) for rowid in sorted(set(rowids))], exp.Null()]) if rowids else exp.false())
    physical = physical.where(restriction)
    if rowids:
        # A native row-ID range avoids the costly payload scan observed with IN alone.
        physical = physical.where(exp.Between(this=exp.column("rowid"),
            low=exp.Literal.number(min(rowids)), high=exp.Literal.number(max(rowids))), append=True)
    rewritten = statement.copy()
    original = rewritten.args["from_"].this
    replacement = physical.subquery()
    replacement.set("alias", exp.TableAlias(this=anchor.content_column.args["table"].copy()))
    original.replace(replacement)
    return TextIndexRewrite(rewritten.sql(dialect="duckdb"), len(rows))
