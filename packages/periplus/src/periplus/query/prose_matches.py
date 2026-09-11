"""Experimental bounded prose previews, consumed before joining capture history."""
from dataclasses import dataclass

from sqlglot import exp

from periplus.platform.catalogue.public import public_objects
from periplus.query.content_scope import ContentScope, _number_parameters, _source
from periplus.query.validation import _one_statement

MAX_MATCHES = 100_000
MAX_MATCH_BYTES = 8 * 1024 * 1024
_PREFIX = '__periplus_prose_matches_'


@dataclass(frozen=True)
class ProseMatches:
    selection_sql: str
    selection_parameters: dict[str, object]
    sql: str
    pattern: str

    def matches(self, connection, installed: dict[str, str]) -> bool:
        definitions = {name: _source('views/' + name + '.sql') for name in ('capture', 'prose')}
        if not ContentScope(self.sql, definitions, '').matches(connection, installed):
            return False
        # An invalid regex must not be eagerly evaluated on unmatched prose.
        return bool(connection.execute(
            "SELECT TRY(regexp_matches('', pattern)) IS NOT NULL FROM unnest(?) p(pattern)",
            [[self.pattern]],
        ).fetchone()[0])

    def select(self, connection) -> dict[str, object] | None:
        cursor = connection.execute(self.selection_sql, self.selection_parameters)
        keys: list[str | None] = []
        previews: list[str | None] = []
        size = 0
        while (row := cursor.fetchone()) is not None:
            if len(keys) == MAX_MATCHES:
                return None
            if any(value is not None and not isinstance(value, str) for value in row):
                return None
            size += sum(len(value.encode()) for value in row if value is not None)
            if size > MAX_MATCH_BYTES:
                return None
            keys.append(row[0])
            previews.append(row[1])
        return {'1': keys, '2': previews}


def prose_matches(sql: str, parameters: list[object] | tuple[object, ...] = ()) -> ProseMatches | None:
    numbered = _number_parameters(sql)
    if numbered is None:
        return None
    tree = _one_statement(numbered)
    if not isinstance(tree, exp.Select) or len(list(tree.walk())) > 1500:
        return None
    if any(v for k, v in tree.args.items() if k not in {'expressions', 'from_', 'joins', 'where'}):
        return None
    source = tree.args.get('from_')
    joins = tree.args.get('joins') or []
    if source is None or len(joins) != 1:
        return None
    join = joins[0]
    if (join.args.get('side') or join.args.get('method') or join.args.get('on')
            or join.args.get('kind') not in (None, '', 'INNER')
            or [x.name for x in join.args.get('using') or []] != ['content_id']):
        return None
    tables = [source.this, join.this]
    if not all(isinstance(t, exp.Table) for t in tables) or sorted(t.name for t in tables) != ['capture', 'prose']:
        return None
    if any(t.catalog or t.db not in ('', 'public_v1') or t.alias_column_names
           or any(v for k, v in t.args.items() if k not in {'this', 'db', 'catalog', 'alias'}) for t in tables):
        return None
    capture = next(t for t in tables if t.name == 'capture')
    prose = next(t for t in tables if t.name == 'prose')
    if capture.alias_or_name.lower() == prose.alias_or_name.lower():
        return None
    if any(i.name.lower().startswith(_PREFIX) for i in tree.find_all(exp.Identifier)):
        return None

    def prose_text(node):
        return (isinstance(node, exp.Column) and node.name == 'text'
                and node.table == prose.alias_or_name and not node.db and not node.catalog)

    where = tree.args.get('where')
    regex = where.this if where else None
    if not isinstance(regex, exp.RegexpLike) or regex.args.get('flag'):
        return None
    text = regex.this.this if isinstance(regex.this, exp.Lower) else regex.this
    if not prose_text(text):
        return None
    bindings = {str(i): value for i, value in enumerate(parameters, 1)}
    pattern = regex.expression
    if isinstance(pattern, exp.Literal) and pattern.is_string:
        value = pattern.this
    elif isinstance(pattern, exp.Placeholder):
        value = bindings.get(pattern.name)
    else:
        return None
    if not isinstance(value, str) or len(value) > 4096:
        return None
    if {p.name for p in tree.find_all(exp.Placeholder)} != set(bindings):
        return None
    previews = [p for p in tree.expressions if isinstance(p, exp.Alias) and isinstance(p.this, exp.Left)]
    if len(previews) != 1:
        return None
    preview = previews[0]
    length = preview.this.expression
    if (not prose_text(preview.this.this) or not isinstance(length, exp.Literal)
            or length.is_string or not length.this.isdigit() or not 0 <= int(length.this) <= 10_000):
        return None
    capture_columns = next(o.columns for o in public_objects() if o.name == 'capture')
    for projection in tree.expressions:
        if projection is preview:
            continue
        column = projection.this if isinstance(projection, exp.Alias) else projection
        if (not isinstance(column, exp.Column) or column.table != capture.alias_or_name
                or column.name not in capture_columns or column.db or column.catalog):
            return None
    # Generated columns cannot participate in user alias resolution: predicates
    # are restricted to the qualified prose text, and ordering/grouping is absent.
    producer = exp.select(exp.column('content_id', table=prose.alias_or_name), preview.this.copy()).from_(prose.copy()).where(where.this.copy()).limit(MAX_MATCHES + 1)
    projection_name = _PREFIX + 'preview'
    preview.set('this', exp.column(projection_name, table=prose.alias_or_name))
    relation = _one_statement(f'SELECT unnest($1::VARCHAR[]) AS content_id, unnest($2::VARCHAR[]) AS {projection_name}')
    prose.replace(exp.Subquery(this=relation, alias=exp.TableAlias(this=exp.to_identifier(prose.alias_or_name, quoted=True))))
    tree.set('where', None)
    return ProseMatches(producer.sql(dialect='duckdb'), bindings, tree.sql(dialect='duckdb'), value)
