"""Internal expansion of authoritative catalogue macro definitions."""

from __future__ import annotations

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from .errors import (
    OptimizationCode,
    OptimizationDiagnostic,
    QueryOptimizationUnavailable,
)
from .purpose import (
    ScalarMacroDefinition,
    ScalarFunctionDefinition,
    TableMacroDefinition,
    ViewDefinition,
)

_MAX_EXPANSION_DEPTH = 32
_VOLATILE_FUNCTION_TYPES = (
    exp.CurrentDate,
    exp.CurrentDatetime,
    exp.CurrentTime,
    exp.CurrentTimestamp,
    exp.CurrentUser,
    exp.NextValueFor,
    exp.Rand,
    exp.SessionUser,
    exp.Uuid,
)
_VOLATILE_FUNCTION_NAMES = frozenset(
    {
        "currval",
        "error",
        "gen_random_uuid",
        "nextval",
        "now",
        "setval",
        "today",
    }
)


def bound_scalar_macro_inputs(
    query: exp.Query,
    *,
    scalar_definitions: tuple[ScalarMacroDefinition, ...],
    table_definitions: tuple[TableMacroDefinition, ...],
    view_definitions: tuple[ViewDefinition, ...] = (),
    scalar_functions: tuple[ScalarFunctionDefinition, ...] = (),
) -> tuple[exp.Query, bool]:
    """Select a bounded driving slice before safe scalar macro evaluation."""

    if not isinstance(query, exp.Select):
        return query, False
    if (
        query.args.get("limit") is None
        or query.args.get("with_") is not None
        or query.args.get("joins")
        or any(
            query.args.get(argument) is not None
            for argument in (
                "distinct",
                "group",
                "having",
                "qualify",
                "windows",
            )
        )
    ):
        return query, False
    from_clause = query.args.get("from_")
    if from_clause is None or not isinstance(from_clause.this, exp.Table):
        return query, False
    source = from_clause.this
    if not isinstance(source.this, exp.Identifier):
        return query, False
    known_scalar_names = {
        (definition.schema_name.lower(), definition.macro_name.lower())
        for definition in scalar_definitions
    }
    if not any(
        isinstance(dot.this, exp.Identifier)
        and isinstance(dot.expression, exp.Anonymous)
        and (
            dot.this.name.lower(),
            dot.expression.name.lower(),
        )
        in known_scalar_names
        for selection in query.expressions
        for dot in selection.find_all(exp.Dot)
    ):
        return query, False
    order = query.args.get("order")
    if order is not None and not all(
        isinstance(
            ordered.this if isinstance(ordered, exp.Ordered) else ordered,
            exp.Column,
        )
        for ordered in order.expressions
    ):
        return query, False

    # Inspect the recursively expanded expression rather than trusting macro
    # ownership or naming. Volatility and explicit error functions make
    # evaluation-order changes observable, so Atlas declines the rewrite.
    expanded_probe = expand_catalogue_macros(
        query,
        scalar_definitions=scalar_definitions,
        table_definitions=table_definitions,
        view_definitions=view_definitions,
    )
    if any(
        isinstance(node, _VOLATILE_FUNCTION_TYPES)
        or (
            isinstance(node, exp.Anonymous)
            and node.name.lower() in _VOLATILE_FUNCTION_NAMES
        )
        for node in expanded_probe.walk()
    ):
        return query, False
    approved_anonymous = {
        definition.function_name.lower()
        for definition in scalar_functions
        if not definition.has_side_effects
        and (definition.stability or "").lower() != "volatile"
    }
    if any(
        node.name.lower() not in approved_anonymous
        for node in expanded_probe.find_all(exp.Anonymous)
    ):
        return query, False

    rewritten = query.copy()
    rewritten_from = rewritten.args["from_"]
    rewritten_source = rewritten_from.this
    assert isinstance(rewritten_source, exp.Table)
    cte_name = _unused_selected_name(rewritten)
    selected = exp.select("*").from_(rewritten_source.copy())
    for argument in ("where", "order", "limit", "offset"):
        value = rewritten.args.get(argument)
        if value is not None:
            selected.set(argument, value.copy())
            if argument != "order":
                rewritten.set(argument, None)
    cte = exp.CTE(
        this=selected,
        alias=exp.TableAlias(this=exp.to_identifier(cte_name)),
        materialized=True,
    )
    rewritten.set("with_", exp.With(expressions=[cte]))
    source_alias = rewritten_source.alias_or_name
    rewritten_from.set(
        "this",
        exp.Table(
            this=exp.to_identifier(cte_name),
            alias=exp.TableAlias(
                this=exp.to_identifier(source_alias)
            ),
        ),
    )
    for dot in rewritten.find_all(exp.Dot):
        function = dot.expression
        if (
            not isinstance(dot.this, exp.Identifier)
            or not isinstance(function, exp.Anonymous)
            or (
                dot.this.name.lower(),
                function.name.lower(),
            )
            not in known_scalar_names
        ):
            continue

        def qualify_argument_column(
            node: exp.Expression,
        ) -> exp.Expression:
            if isinstance(node, exp.Column) and not node.table:
                qualified = node.copy()
                qualified.set(
                    "table",
                    exp.to_identifier(source_alias),
                )
                return qualified
            return node

        function.set(
            "expressions",
            [
                argument.copy().transform(qualify_argument_column)
                for argument in function.expressions
            ],
        )
    return rewritten, True


def _unused_selected_name(query: exp.Query) -> str:
    used = {
        cte.alias_or_name.lower()
        for cte in query.find_all(exp.CTE)
        if cte.alias_or_name
    }
    candidate = "_atlas_selected"
    index = 2
    while candidate.lower() in used:
        candidate = f"_atlas_selected_{index}"
        index += 1
    return candidate


def expand_catalogue_macros(
    query: exp.Query,
    *,
    scalar_definitions: tuple[ScalarMacroDefinition, ...],
    table_definitions: tuple[TableMacroDefinition, ...],
    view_definitions: tuple[ViewDefinition, ...] = (),
) -> exp.Query:
    """Return a copy with known `macros.*` calls expanded into ordinary SQL."""

    expanded = query.copy()
    scalar = _definitions_by_name(scalar_definitions)
    table = _definitions_by_name(table_definitions)
    views = _views_by_name(view_definitions)
    for _ in range(_MAX_EXPANSION_DEPTH):
        changed = _expand_view_references(expanded, views)
        changed = _expand_table_calls(expanded, table) or changed
        changed = _expand_scalar_calls(expanded, scalar) or changed
        if not changed:
            _reject_unresolved_macros(expanded)
            return expanded
    _unavailable(
        OptimizationCode.UNSUPPORTED_FUNCTION,
        "Catalogue macro expansion exceeded its nesting limit.",
        documentation_anchor="macro-expansion",
    )


def _views_by_name(
    definitions: tuple[ViewDefinition, ...],
) -> dict[tuple[str, str], ViewDefinition]:
    result: dict[tuple[str, str], ViewDefinition] = {}
    for definition in definitions:
        key = (
            definition.schema_name.lower(),
            definition.view_name.lower(),
        )
        if key in result:
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                "View definitions must have unique qualified names.",
                sql_fragment=".".join(key),
                documentation_anchor="view-expansion",
            )
        result[key] = definition
    return result


def _expand_view_references(
    query: exp.Query,
    definitions: dict[tuple[str, str], ViewDefinition],
) -> bool:
    changed = False
    for table in tuple(query.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            continue
        definition = definitions.get(
            (table.db.lower(), table.name.lower())
        )
        if definition is None:
            continue
        try:
            body = parse_one(definition.sql, dialect="duckdb")
        except ParseError as exc:
            _invalid_definition(definition.view_name, exc)
        if not isinstance(body, exp.Query):
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                f"View {definition.view_name!r} must contain a query.",
                documentation_anchor="view-expansion",
            )
        alias = table.args.get("alias")
        effective_alias = (
            alias.copy()
            if alias is not None
            else exp.TableAlias(
                this=exp.to_identifier(definition.view_name)
            )
        )
        table.replace(
            exp.Subquery(
                this=body,
                alias=effective_alias,
            )
        )
        changed = True
    return changed


def _definitions_by_name(definitions: tuple[object, ...]) -> dict[str, object]:
    result: dict[str, object] = {}
    for definition in definitions:
        schema = str(getattr(definition, "schema_name")).lower()
        name = str(getattr(definition, "macro_name")).lower()
        if schema != "macros" or name in result:
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                "Macro definitions must have unique names in the macros schema.",
                sql_fragment=f"{schema}.{name}",
                documentation_anchor="macro-expansion",
            )
        result[name] = definition
    return result


def _reject_unresolved_macros(query: exp.Query) -> None:
    unresolved_table = next(
        (
            table
            for table in query.find_all(exp.Table)
            if isinstance(table.this, exp.Anonymous)
            and table.db.lower() == "macros"
        ),
        None,
    )
    unresolved_scalar = next(
        (
            dot
            for dot in query.find_all(exp.Dot)
            if isinstance(dot.this, exp.Identifier)
            and dot.this.name.lower() == "macros"
            and isinstance(dot.expression, exp.Anonymous)
        ),
        None,
    )
    unresolved = unresolved_table or unresolved_scalar
    if unresolved is not None:
        rendered = unresolved.sql(dialect="duckdb")
        _unavailable(
            OptimizationCode.UNSUPPORTED_FUNCTION,
            f"Atlas has no stored definition for {rendered}.",
            sql_fragment=rendered,
            documentation_anchor="macro-expansion",
        )


def _expand_scalar_calls(
    query: exp.Query,
    definitions: dict[str, object],
) -> bool:
    changed = False
    for dot in tuple(query.find_all(exp.Dot)):
        function = dot.expression
        if (
            not isinstance(dot.this, exp.Identifier)
            or dot.this.name.lower() != "macros"
            or not isinstance(function, exp.Anonymous)
        ):
            continue
        definition = definitions.get(function.name.lower())
        if not isinstance(definition, ScalarMacroDefinition):
            continue
        arguments = _bind_arguments(
            definition.parameters,
            (),
            tuple(function.expressions),
            rendered=dot.sql(dialect="duckdb"),
        )
        try:
            body = parse_one(definition.sql, dialect="duckdb")
        except ParseError as exc:
            _invalid_definition(definition.macro_name, exc)
        dot.replace(_substitute(body, arguments))
        changed = True
    return changed


def _expand_table_calls(
    query: exp.Query,
    definitions: dict[str, object],
) -> bool:
    changed = False
    for table in tuple(query.find_all(exp.Table)):
        function = table.this
        if (
            not isinstance(function, exp.Anonymous)
            or table.db.lower() != "macros"
        ):
            continue
        definition = definitions.get(function.name.lower())
        if not isinstance(definition, TableMacroDefinition):
            continue
        defaults = tuple(
            (name, _parse_expression(sql, definition.macro_name))
            for name, sql in definition.parameter_defaults
        )
        arguments = _bind_arguments(
            definition.parameters,
            defaults,
            tuple(function.expressions),
            rendered=table.sql(dialect="duckdb"),
        )
        try:
            body = parse_one(definition.sql, dialect="duckdb")
        except ParseError as exc:
            _invalid_definition(definition.macro_name, exc)
        if not isinstance(body, exp.Query):
            _unavailable(
                OptimizationCode.UNSUPPORTED_FUNCTION,
                f"Table macro {definition.macro_name!r} must contain a query.",
                documentation_anchor="macro-expansion",
            )
        alias = table.args.get("alias")
        table.replace(
            exp.Subquery(
                this=_substitute(body, arguments),
                alias=alias.copy() if alias is not None else None,
            )
        )
        changed = True
    return changed


def _bind_arguments(
    parameters: tuple[str, ...],
    defaults: tuple[tuple[str, exp.Expression], ...],
    supplied: tuple[exp.Expression, ...],
    *,
    rendered: str,
) -> dict[str, exp.Expression]:
    values = {name.lower(): value.copy() for name, value in defaults}
    position = 0
    for argument in supplied:
        if isinstance(argument, exp.PropertyEQ):
            name = argument.this.name.lower()
            if name not in {parameter.lower() for parameter in parameters}:
                _argument_error(rendered)
            values[name] = argument.expression.copy()
            continue
        if position >= len(parameters):
            _argument_error(rendered)
        values[parameters[position].lower()] = argument.copy()
        position += 1
    missing = [
        parameter
        for parameter in parameters
        if parameter.lower() not in values
    ]
    if missing:
        _argument_error(rendered)
    return values


def _substitute(
    body: exp.Expression,
    arguments: dict[str, exp.Expression],
) -> exp.Expression:
    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table:
            argument = arguments.get(node.name.lower())
            if argument is not None:
                return argument.copy()
        return node

    return body.copy().transform(replace)


def _parse_expression(sql: str, macro_name: str) -> exp.Expression:
    try:
        return parse_one(sql, dialect="duckdb")
    except ParseError as exc:
        _invalid_definition(macro_name, exc)


def _argument_error(rendered: str) -> None:
    _unavailable(
        OptimizationCode.UNSUPPORTED_FUNCTION,
        f"Arguments do not match the stored macro signature for {rendered}.",
        sql_fragment=rendered,
        documentation_anchor="macro-expansion",
    )


def _invalid_definition(name: str, cause: Exception) -> None:
    _unavailable(
        OptimizationCode.UNSUPPORTED_FUNCTION,
        f"Stored SQL for macro {name!r} cannot be expanded.",
        sql_fragment=name,
        documentation_anchor="macro-expansion",
        cause=cause,
    )


def _unavailable(
    code: OptimizationCode,
    message: str,
    *,
    sql_fragment: str | None = None,
    documentation_anchor: str,
    cause: Exception | None = None,
) -> None:
    error = QueryOptimizationUnavailable(
        OptimizationDiagnostic(
            code=code,
            message=message,
            sql_fragment=sql_fragment,
            documentation_anchor=documentation_anchor,
        )
    )
    if cause is not None:
        raise error from cause
    raise error
