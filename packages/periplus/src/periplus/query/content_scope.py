"""Restrict reviewed document-local views to keys selected by a simple join.

Experimental activation is restricted separately from research eligibility.
This is deliberately not a general SQL optimizer. Eligibility is conservative,
and the installed view definitions must match the reviewed catalogue sources.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
import re

from sqlglot import exp, parse_one
from sqlglot.tokens import TokenType

from periplus.platform.catalogue.public import public_objects
from periplus.query.validation import _one_statement, _QueryDuckDB

# Total, deterministic expressions only: copying volatile/error-producing user
# expressions can change evaluations even when the output rows are identical.
_ALLOWED = {
    'select', 'from', 'table', 'tablealias', 'identifier', 'column', 'alias',
    'join', 'where', 'and', 'or', 'not', 'paren', 'eq', 'neq', 'gt', 'gte', 'lt',
    'lte', 'is', 'null', 'boolean', 'literal', 'placeholder', 'parameter', 'var',
    'like', 'ilike', 'in', 'between', 'lower', 'upper', 'coalesce',
    'order', 'ordered', 'distinct', 'limit', 'offset', 'star',
}
_PRIMITIVES = {'html_node', 'html_element'}
_DRIVERS = {'prose', 'capture', *_PRIMITIVES}
_MAX_NODES = 1500
_MAX_SQL = 100_000


@dataclass(frozen=True)
class ContentScope:
    sql: str
    definitions: dict[str, str]
    key_cte: str

    def matches(self, connection, installed: dict[str, str]) -> bool:
        if any(name not in installed for name in self.definitions):
            return False
        sources = []
        for name, source in self.definitions.items():
            for sql in (source, installed[name]):
                # Both strings are CREATE VIEW declarations, not user SQL.
                query = re.split(r"\bAS\b", sql, maxsplit=1, flags=re.IGNORECASE)
                if len(query) != 2:
                    return False
                sources.append(query[1])
        # Let the pinned native parser normalize its own view serialization.
        # These functions only parse/print SQL; they never execute the queries.
        normalized = connection.execute(
            "SELECT json_deserialize_sql(json_serialize_sql(sql)) "
            "FROM unnest(?) WITH ORDINALITY t(sql, position) ORDER BY position",
            [sources],
        ).fetchall()
        return all(normalized[i] == normalized[i + 1] for i in range(0, len(normalized), 2))


@lru_cache(maxsize=64)
def _source(resource: str) -> str:
    return files('periplus.platform.catalogue').joinpath('sql/public_v1', resource).read_text()


def _number_parameters(sql: str) -> str | None:
    tokens = _QueryDuckDB.Tokenizer().tokenize(sql)
    # Existing numbered/named parameter syntax is left untouched by this pass.
    if any(t.token_type == TokenType.PARAMETER for t in tokens):
        return None
    positions = [t for t in tokens if t.token_type == TokenType.PLACEHOLDER]
    if any(t.text != '?' for t in positions):
        return None
    for index, token in reversed(list(enumerate(positions, 1))):
        sql = sql[:token.start] + f'${index}' + sql[token.end + 1:]
    return sql


def _conjuncts(expression: exp.Expression):
    """Unwrap parentheses and AND only; an equality inside OR is not required."""
    expression = expression.unnest()
    if isinstance(expression, exp.And):
        yield from _conjuncts(expression.this)
        yield from _conjuncts(expression.expression)
    else:
        yield expression


def content_scope(sql: str, *, materialize_inputs: bool = False, heading_driver: bool = False) -> ContentScope | None:
    """Produce at most one alternative; unsupported syntax retains original SQL."""
    numbered = _number_parameters(sql)
    if numbered is None:
        return None
    tree = _one_statement(numbered)
    if not isinstance(tree, exp.Select):
        return None
    nodes = list(tree.walk())
    if len(nodes) > _MAX_NODES or any(n.key not in _ALLOWED for n in nodes):
        return None
    if tree.args.get('with_') or not tree.args.get('where'):
        return None
    if tree.args.get('limit') or tree.args.get('offset'):
        return None
    # Preserve DuckDB's original output labels; computed outputs need an alias.
    if any(not isinstance(e, (exp.Column, exp.Star, exp.Alias)) for e in tree.expressions):
        return None
    source = tree.args.get('from_')
    if source is None or not isinstance(source.this, exp.Table):
        return None
    joins = tree.args.get('joins') or []
    if not joins or len(joins) > 8:
        return None
    tables = [source.this, *(j.this for j in joins)]
    registry = {o.name: o for o in public_objects() if o.kind == 'view'}
    if any(not isinstance(t, exp.Table) or t.name not in registry or t.catalog
           or t.db not in ('', 'public_v1') or t.args.get('pivots')
           or t.args.get('sample') or t.args.get('version') for t in tables):
        return None
    aliases = [t.alias_or_name for t in tables]
    if any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z_0-9]*', a) for a in aliases):
        return None
    if any(c.db or c.catalog for c in tree.find_all(exp.Column)):
        return None
    if len(set(a.lower() for a in aliases)) != len(aliases):
        return None
    if any(t.args.get('alias') and t.args['alias'].args.get('columns') for t in tables):
        return None
    # Every join must link its new source to an earlier source by the complete
    # document key in a required conjunct. Keep the entire ON expression in the
    # final join: residual predicates must not shrink a view's window partitions.
    seen = {aliases[0]}
    for join, alias in zip(joins, aliases[1:]):
        if join.args.get('side') or join.args.get('kind') not in (None, '', 'INNER') or join.args.get('method'):
            return None
        using = join.args.get('using')
        if using:
            if [x.name for x in using] != ['content_id']:
                return None
        else:
            on = join.args.get('on')
            if on is None:
                return None
            connected = False
            for conjunct in _conjuncts(on):
                if not isinstance(conjunct, exp.EQ):
                    continue
                a, b = conjunct.this.unnest(), conjunct.expression.unnest()
                if (isinstance(a, exp.Column) and isinstance(b, exp.Column)
                        and a.name == b.name == 'content_id'
                        and ((a.table == alias and b.table in seen) or (b.table == alias and a.table in seen))):
                    connected = True
                    break
            if not connected:
                return None
        seen.add(alias)
    if any(t.name not in _DRIVERS and not registry[t.name].content_local for t in tables):
        return None
    # The filtered source can occur anywhere in this connected inner-join
    # chain. Select one eligible driver without changing the user's join order.
    where = tree.args['where'].this
    predicates = list(where.flatten()) if isinstance(where, exp.And) else [where]
    for driver in tables:
        if driver.name not in ({"html_heading"} if heading_driver else _DRIVERS):
            continue
        filters, remaining = [], []
        for predicate in predicates:
            columns = list(predicate.find_all(exp.Column))
            if columns and all(c.table == driver.alias_or_name and c.name in registry[driver.name].columns for c in columns):
                filters.append(predicate.copy())
            else:
                remaining.append(predicate.copy())
        if filters:
            break
    else:
        return None
    targets = [t for t in tables if t is not driver and registry[t.name].content_local]
    if not targets:
        return None
    # No names from the request may be captured by generated CTEs, including
    # qualified/unqualified columns and output aliases.
    identifiers = {n.name.lower() for n in nodes if isinstance(n, exp.Identifier)}
    prefix = '__periplus_scope_'
    while any(x.startswith(prefix) for x in identifiers):
        prefix = '_' + prefix
    selected, keys = prefix+'selected', prefix+'keys'
    definitions = {driver.name: _source(registry[driver.name].resource)}
    ctes = []
    candidate = exp.select(exp.Column(this=exp.Star(), table=exp.to_identifier(driver.alias_or_name, quoted=True))).from_(driver.copy()).where(exp.and_(*filters))
    ctes.append(exp.CTE(this=candidate, alias=exp.TableAlias(this=exp.to_identifier(selected)), materialized=True))
    ctes.append(exp.CTE(this=exp.select('content_id').distinct().from_(selected),
                       alias=exp.TableAlias(this=exp.to_identifier(keys))))
    primitive_names = {}

    def expand(name: str, stack: tuple[str, ...] = ()) -> exp.Query:
        if name in stack or len(stack) > 8:
            raise ValueError('cyclic or excessive catalogue expansion')
        item = registry[name]
        if not item.content_local:
            raise ValueError('unreviewed catalogue dependency')
        definitions[name] = _source(item.resource)
        if name in _PRIMITIVES:
            if name not in primitive_names:
                primitive_names[name] = prefix+name
                restricted = parse_one(
                    f'SELECT b.* FROM public_v1.{name} b SEMI JOIN {keys} USING (content_id)', read='duckdb')
                ctes.append(exp.CTE(this=restricted, alias=exp.TableAlias(this=exp.to_identifier(primitive_names[name])), materialized=materialize_inputs))
            return exp.select('*').from_(primitive_names[name])
        body = parse_one(definitions[name], read='duckdb').expression.copy()
        for table in list(body.find_all(exp.Table)):
            if table.db == 'public_v1':
                sub = expand(table.name, (*stack, name))
                table.replace(sub.subquery(alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name, quoted=True))))
        return body

    try:
        for table in targets:
            table.replace(expand(table.name).subquery(alias=exp.TableAlias(this=exp.to_identifier(table.alias_or_name, quoted=True))))
    except (KeyError, ValueError):
        return None
    driver.replace(exp.Table(this=exp.to_identifier(selected), alias=exp.TableAlias(this=exp.to_identifier(driver.alias_or_name, quoted=True))))
    # Selected rows already satisfy these predicates. Leaving them above the
    # CTE would force it to carry large prose text solely to test it a second time.
    tree.set('where', exp.Where(this=exp.and_(*remaining)) if remaining else None)
    tree.set('with_', exp.With(expressions=ctes))
    result = tree.sql(dialect='duckdb')
    if len(result) > _MAX_SQL:
        return None
    return ContentScope(result, definitions, keys)


def experimental_scope(sql: str, parameters: list[object] | tuple[object, ...] = ()) -> ContentScope | None:
    """Activate only the measured exact-URL capture/heading inner-join family."""
    if any(not isinstance(value, str) for value in parameters):
        return None
    tree = _one_statement(sql)
    if not isinstance(tree, exp.Select) or tree.args.get('with_'):
        return None
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 2 or sorted(t.name for t in tables) != ['capture', 'html_heading']:
        return None
    capture = next(t for t in tables if t.name == 'capture')
    where = tree.args.get('where')
    if where is None:
        return None
    predicates = list(_conjuncts(where.this))
    if len(predicates) != 1:
        return None
    joins = tree.args.get('joins') or []
    if len(joins) != 1:
        return None
    join = joins[0]
    if not join.args.get('using'):
        on = join.args.get('on')
        if on is None or not isinstance(on.unnest(), exp.EQ):
            return None
        if not all(isinstance(c, exp.Column) and c.name == 'content_id' for c in (on.unnest().this, on.unnest().expression)):
            return None
    for predicate in predicates:
        if not isinstance(predicate, exp.EQ):
            continue
        for column, value in ((predicate.this.unnest(), predicate.expression.unnest()),
                              (predicate.expression.unnest(), predicate.this.unnest())):
            if (isinstance(column, exp.Column) and column.table == capture.alias_or_name
                    and column.name == 'effective_url'
                    and ((isinstance(value, exp.Literal) and value.is_string)
                         or isinstance(value, exp.Placeholder))):
                return content_scope(sql)
    return None
