"""Measured broad latest-page regex counts: carry scalars through capture joins."""
from dataclasses import dataclass

from sqlglot import exp

from periplus.platform.catalogue.public import public_objects
from periplus.query.content_scope import ContentScope, _source
from periplus.query.validation import _one_statement

# Bound the supported grammar, including the unchanged outer query: round-tripping
# arbitrary DuckDB extensions through a different parser is not a semantic proof.
_ALLOWED = {
    'select', 'with', 'cte', 'from', 'table', 'tablealias', 'identifier',
    'column', 'alias', 'join', 'distinct', 'tuple', 'order', 'ordered',
    'literal', 'length', 'regexpextractall', 'splitpart', 'where', 'gt',
    'group', 'sum', 'count', 'star', 'limit',
}


@dataclass(frozen=True)
class ProseScalar:
    sql: str
    pattern: str

    def matches(self, connection, installed: dict[str, str]) -> bool:
        definitions = {
            o.name: _source(o.resource) for o in public_objects()
            if o.name in {'capture', 'prose'}
        }
        if not ContentScope(self.sql, definitions, '').matches(connection, installed):
            return False
        # Moving an invalid regex could introduce an error on unmatched contents.
        # Compile the literal with the executing engine, without scanning any data.
        # A one-row relation avoids constant folding outside TRY, which would
        # abort the caller's read transaction for an invalid pattern.
        return bool(connection.execute(
            "SELECT TRY(regexp_extract_all('', pattern)) IS NOT NULL "
            "FROM unnest(?) p(pattern)", [[self.pattern]],
        ).fetchone()[0])


def prose_scalar(sql: str, parameters: list[object] | tuple[object, ...] = ()) -> ProseScalar | None:
    """Rewrite only broad capture/prose latest-URL blocks; selective work stays native."""
    if parameters:
        return None
    tree = _one_statement(sql)
    nodes = list(tree.walk())
    if (not isinstance(tree, exp.Select) or len(nodes) > 1500
            or any(n.key not in _ALLOWED for n in nodes)):
        return None
    with_ = tree.args.get('with_')
    if with_ is None or with_.args.get('recursive') or len(with_.expressions) != 1:
        return None
    cte = with_.expressions[0]
    if cte.alias_column_names or cte.args.get('materialized') is not None:
        return None
    page = cte.this
    if not isinstance(page, exp.Select) or len(list(tree.find_all(exp.Select))) != 2:
        return None
    tables = list(tree.find_all(exp.Table))
    if len(tables) != 3 or tree.args.get('joins'):
        return None
    outer_source = tree.args.get('from_')
    if (outer_source is None or not isinstance(outer_source.this, exp.Table)
            or outer_source.this.name != cte.alias or outer_source.this.db):
        return None
    # The measured family processes all captures before latest selection. An eager
    # prose CTE can regress selective joins, limits, and predicates; exclude them.
    if any(v for k, v in page.args.items() if k not in {
        'expressions', 'from_', 'joins', 'distinct', 'order'
    }):
        return None
    joins = page.args.get('joins') or []
    source = page.args.get('from_')
    if len(joins) != 1 or source is None:
        return None
    join = joins[0]
    if (join.args.get('side') or join.args.get('method')
            or join.args.get('kind') not in (None, '', 'INNER')
            or join.args.get('on')
            or [x.name for x in join.args.get('using') or []] != ['content_id']):
        return None
    sources = [source.this, join.this]
    if (not all(isinstance(t, exp.Table) for t in sources)
            or sorted(t.name for t in sources) != ['capture', 'prose']):
        return None
    if any(t.catalog or t.db not in ('', 'public_v1') or t.alias_column_names
           or any(v for k, v in t.args.items() if k not in {'this', 'db', 'catalog', 'alias'})
           for t in sources):
        return None
    # Never mistake a shadowing CTE for a public relation.
    if cte.alias.lower() in {'capture', 'prose'}:
        return None
    capture = next(t for t in sources if t.name == 'capture')
    prose = next(t for t in sources if t.name == 'prose')
    if capture.alias_or_name.lower() == prose.alias_or_name.lower():
        return None
    def capture_column(node, name):
        return (isinstance(node, exp.Column) and node.name == name
                and not node.db and not node.catalog
                and node.table in ('', capture.alias_or_name))

    distinct = page.args.get('distinct')
    on = distinct.args.get('on') if distinct else None
    order = page.args.get('order')
    if (on is None or len(on.expressions) != 1
            or not capture_column(on.expressions[0], 'requested_url')
            or order is None or len(order.expressions) != 3):
        return None
    for item, name in zip(order.expressions, ('requested_url', 'captured_at', 'capture_id')):
        if not capture_column(item.this, name):
            return None
        if name != 'requested_url' and not item.args.get('desc'):
            return None
    scalars = [p for p in page.expressions if isinstance(p, exp.Alias)
               and isinstance(p.this, exp.Length)
               and isinstance(p.this.this, exp.RegexpExtractAll)]
    if len(scalars) != 1:
        return None
    scalar = scalars[0]
    regex = scalar.this.this
    text = regex.this
    pattern = regex.expression
    if (not isinstance(text, exp.Column) or text.name != 'text'
            or text.table not in ('', prose.alias_or_name) or text.db or text.catalog
            or not isinstance(pattern, exp.Literal) or not pattern.is_string
            or len(pattern.this) > 256
            or regex.args.get('parameters')
            or regex.args.get('group') != exp.Literal.number(0)):
        return None
    # No raw text or other prose payload may escape the computed scalar.
    columns = next(o.columns for o in public_objects() if o.name == 'capture')
    for col in page.find_all(exp.Column):
        if col is text:
            continue
        if col.name not in columns or not capture_column(col, col.name):
            return None
    if any(isinstance(n, exp.Star) for n in page.walk()):
        return None
    # Keep output labels explicit, unique and distinct from input names, so DuckDB
    # alias resolution cannot change when the generated scalar enters the join.
    aliases = [p.alias for p in page.expressions if isinstance(p, exp.Alias)]
    if (len(aliases) != len(page.expressions) or len(set(a.lower() for a in aliases)) != len(aliases)
            or any(a.lower() in {*columns, 'text'} for a in aliases)):
        return None
    identifiers = {n.name.lower() for n in tree.find_all(exp.Identifier)}
    prefix = '__periplus_prose_scalar'
    if any(name.startswith(prefix) for name in identifiers):
        return None
    count_name = prefix + '_value'
    computed = scalar.this.copy()
    computed.find(exp.Column).set('table', None)
    producer = exp.select('content_id', exp.alias_(computed, count_name)).from_('public_v1.prose')
    scalar.set('this', exp.column(count_name, table=prose.alias_or_name))
    prose.set('this', exp.to_identifier(prefix))
    prose.set('db', None)
    if not prose.args.get('alias'):
        prose.set('alias', exp.TableAlias(this=exp.to_identifier('prose')))
    with_.expressions.insert(0, exp.CTE(
        this=producer, alias=exp.TableAlias(this=exp.to_identifier(prefix)), materialized=True,
    ))
    result = tree.sql(dialect='duckdb')
    return ProseScalar(result, pattern.this) if len(result) <= 100_000 else None
