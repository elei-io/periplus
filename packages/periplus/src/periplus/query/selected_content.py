"""Experimental, execution-only selection of complete content partitions.

Only capture-driven SELECT blocks qualify. Every extracted relation must join
its content ID directly to the selected capture CTE. No discovery runs in prep.
"""
from dataclasses import dataclass
import re

from sqlglot import exp

from periplus.query.content_scope import ContentScope, _number_parameters, _source
from periplus.query.validation import _one_statement

MAX_KEYS = 100_000
MAX_KEY_BYTES = 8 * 1024 * 1024
_PREFIX = '__periplus_selected_'
_VIEWS = {'html_heading', 'html_section', 'html_metadata'}
_ALLOWED = set('select with cte from table tablealias identifier column alias join star where and or not paren eq neq gt gte lt lte like ilike is null in literal boolean placeholder parameter var cast datatype arraycontains lower upper trim left substring length coalesce replace min max sum count group order ordered limit distinct tuple'.split())


@dataclass(frozen=True)
class SelectedContent:
    selection_sql: str
    sql: str
    selection_parameters: dict[str, object]
    parameters: dict[str, object]
    key_parameter: str
    definitions: dict[str, str]

    def matches(self, connection, installed: dict[str, str]) -> bool:
        return ContentScope(self.sql, self.definitions, '').matches(connection, installed)

    def select(self, connection) -> list[str] | None:
        """Bound Python collection as well as SQL output; None means native execution."""
        cursor = connection.execute(self.selection_sql, self.selection_parameters)
        keys: list[str] = []
        size = 0
        seen: set[str] = set()
        input_rows = 0
        while (row := cursor.fetchone()) is not None:
            input_rows += 1
            if input_rows > MAX_KEYS:
                return None
            key = row[0]
            if not isinstance(key, str):
                return None
            if key in seen:
                continue
            seen.add(key)
            size += len(key.encode())
            if len(keys) == MAX_KEYS or size > MAX_KEY_BYTES:
                return None
            keys.append(key)
        return keys


def _content_join(join: exp.Join, alias: str, driver: str) -> bool:
    if join.args.get('method') or join.args.get('kind') not in (None, '', 'INNER'):
        return False
    if join.args.get('side') not in (None, '', 'LEFT'):
        return False
    if [x.name for x in join.args.get('using', [])] == ['content_id']:
        return True
    def conjuncts(node):
        if isinstance(node, exp.Paren):
            return conjuncts(node.this)
        if isinstance(node, exp.And):
            return conjuncts(node.this) + conjuncts(node.expression)
        return [node]
    return any(isinstance(n, exp.EQ) and isinstance(n.this, exp.Column)
               and isinstance(n.expression, exp.Column)
               and {(n.this.table, n.this.name), (n.expression.table, n.expression.name)}
               == {(driver, 'content_id'), (alias, 'content_id')}
               for n in conjuncts(join.args.get('on')))


def selected_content(sql: str, parameters: list[object] | tuple[object, ...] = ()) -> SelectedContent | None:
    numbered = _number_parameters(sql)
    if numbered is None:
        return None
    tree = _one_statement(numbered)
    nodes = list(tree.walk())
    if (not isinstance(tree, exp.Select) or len(nodes) > 1500
            or any(n.key not in _ALLOWED for n in nodes)
            or any(i.name.lower().startswith(_PREFIX) for i in tree.find_all(exp.Identifier))):
        return None
    with_ = tree.args.get('with_')
    if not with_ or with_.args.get('recursive') or not 1 <= len(with_.expressions) <= 4:
        return None
    ctes = with_.expressions
    if any(c.alias_column_names or not isinstance(c.this, exp.Select) for c in ctes):
        return None
    if len(list(tree.find_all(exp.Select))) != len(ctes) + 1:
        return None
    driver_name = ctes[0].alias
    driver = ctes[0].this
    source = driver.args.get('from_')
    if (not source or not isinstance(source.this, exp.Table) or source.this.name != 'capture'
            or driver.args.get('joins') or driver.args.get('group') or driver.args.get('distinct')
            or driver.args.get('limit') or not driver.args.get('where')):
        return None
    # Direct projection is the proof that this key names the captured document.
    if not any(isinstance(p, exp.Column) and p.name == 'content_id' for p in driver.expressions):
        return None
    if any(isinstance(p, (exp.Star, exp.Alias)) or not isinstance(p, exp.Column) for p in driver.expressions):
        return None
    # Only literal/parameter UUID casts (request IDs), never movable fallible casts.
    if any(not isinstance(c.this, (exp.Literal, exp.Placeholder)) or c.to.sql().upper() != 'UUID'
           for c in tree.find_all(exp.Cast)):
        return None
    extracted = []
    cte_names = {c.alias for c in ctes}
    if len(cte_names) != len(ctes) or cte_names & (_VIEWS | {'capture'}):
        return None
    for block in [*(c.this for c in ctes[1:]), tree]:
        from_ = block.args.get('from_')
        if not from_ or not isinstance(from_.this, exp.Table) or from_.this.name != driver_name:
            return None
        driver_alias = from_.this.alias_or_name
        for join in block.args.get('joins', []):
            table = join.this
            if not isinstance(table, exp.Table):
                return None
            if table.name in _VIEWS:
                if not _content_join(join, table.alias_or_name, driver_alias):
                    return None
                extracted.append(table)
            elif (table.name not in cte_names or join.args.get('side') not in (None, '', 'LEFT')
                  or join.args.get('kind') not in (None, '', 'INNER')
                  or [x.name for x in join.args.get('using', [])] != ['capture_id']):
                return None
    if not extracted or not any(t.name.startswith('html_') for t in extracted):
        return None
    if any(t.catalog or t.db not in ('', 'public_v1') for t in tree.find_all(exp.Table)):
        return None
    bindings = {str(i): value for i, value in enumerate(parameters, 1)}
    used = {p.name for p in tree.find_all(exp.Placeholder)}
    if used != set(bindings):
        return None
    key_parameter = str(len(parameters) + 1)
    definitions = {n: _source('views/' + n + '.sql') for n in {'capture', *(t.name for t in extracted)}}
    key_filter = f'content_id IN (SELECT unnest(${key_parameter})) AND list_contains(${key_parameter}, content_id)'
    dependencies = []
    needed = {t.name for t in extracted}
    if needed & {'html_heading', 'html_section', 'html_metadata'}:
        columns = 'content_id,node_index,subtree_end_index,tag,namespace'
        if 'html_metadata' in needed:
            columns += ',attributes,text_direct'
        dependencies.append(('html_element', columns))
    if needed & {'html_heading', 'html_section'}:
        dependencies.append(('html_node', 'content_id,node_index,subtree_end_index,node_type,value'))
    generated = []
    for name, columns in dependencies:
        definitions[name] = _source('views/' + name + '.sql')
        body = _one_statement(f'SELECT {columns} FROM public_v1.{name} WHERE {key_filter}')
        generated.append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(_PREFIX + name)), materialized=True))
    for name in sorted(needed):
        body = _one_statement(re.split(r'\bAS\b', definitions[name], maxsplit=1, flags=re.I)[1])
        for table in body.find_all(exp.Table):
            if table.name in {'html_element', 'html_node'}:
                table.set('this', exp.to_identifier(_PREFIX + table.name))
                table.set('db', None)
        generated.append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(_PREFIX + name)), materialized=True))
    selector = f'SELECT content_id FROM ({driver.sql(dialect="duckdb")}) AS selected WHERE content_id IS NOT NULL LIMIT {MAX_KEYS + 1}'
    selection_bindings = {p.name: bindings[p.name] for p in driver.find_all(exp.Placeholder)}
    for table in extracted:
        if not table.alias:
            table.set('alias', exp.TableAlias(this=exp.to_identifier(table.name)))
        table.set('this', exp.to_identifier(_PREFIX + table.name))
        table.set('db', None)
    ctes[0].set('materialized', True)
    with_.set('expressions', [ctes[0], *generated, *ctes[1:]])
    return SelectedContent(selector, tree.sql(dialect='duckdb'), selection_bindings, bindings, key_parameter, definitions)
