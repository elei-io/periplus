"""Catalogue-aware scan recognition and conservative cost estimates."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse_one

from .metadata import (
    BoundParameter,
    CatalogueMetadataSnapshot,
    CompilationEstimate,
    ManagedTableMetadata,
    ScanEstimate,
)
from .metadata_aggregates import metadata_only_row_count_table
from .streaming_limit import direct_streaming_limit


def estimate_compilation(
    authored_sql: str,
    executable_sql: str,
    *,
    metadata: CatalogueMetadataSnapshot,
    bound_parameters: tuple[BoundParameter, ...] = (),
) -> CompilationEstimate:
    """Estimate physical scan reduction without executing either query."""

    parameters = {
        parameter.name.lower(): parameter.value
        for parameter in bound_parameters
    }
    authored = _estimate_query(authored_sql, metadata, parameters)
    executable = _estimate_query(executable_sql, metadata, parameters)
    return CompilationEstimate(
        authored_scans=authored,
        executable_scans=executable,
        estimated_rows_avoided=_difference(
            authored,
            executable,
            "estimated_rows_read",
        ),
        estimated_bytes_avoided=_difference(
            authored,
            executable,
            "estimated_bytes_read",
        ),
    )


def add_document_scope_estimate(
    estimate: CompilationEstimate,
    *,
    elements: ManagedTableMetadata,
    maximum_documents: int,
    maximum_elements: int,
) -> CompilationEstimate:
    """Add the conservative upper bound for one runtime-hydrated elements scan."""

    rows = (
        min(elements.estimated_rows, maximum_elements)
        if elements.estimated_rows is not None
        else maximum_elements
    )
    bytes_read = (
        max(
            1,
            round(
                elements.file_size_bytes
                * rows
                / elements.estimated_rows
            ),
        )
        if elements.file_size_bytes is not None
        and elements.estimated_rows is not None
        and elements.estimated_rows > 0
        else None
    )
    executable = (
        *estimate.executable_scans,
        ScanEstimate(
            relation=elements.qualified_name,
            table_uuid=elements.table_uuid,
            estimated_rows_read=rows,
            estimated_bytes_read=bytes_read,
            partition_predicates=(
                "runtime document_id scope "
                f"(at most {maximum_documents:,} documents)",
            ),
        ),
    )
    return CompilationEstimate(
        authored_scans=estimate.authored_scans,
        executable_scans=executable,
        estimated_rows_avoided=_difference(
            estimate.authored_scans,
            executable,
            "estimated_rows_read",
        ),
        estimated_bytes_avoided=_difference(
            estimate.authored_scans,
            executable,
            "estimated_bytes_read",
        ),
    )


def _estimate_query(
    sql: str,
    metadata: CatalogueMetadataSnapshot,
    parameters: dict[str, object],
) -> tuple[ScanEstimate, ...]:
    query = parse_one(sql, dialect="duckdb")
    metadata_count_table = metadata_only_row_count_table(query)
    streaming_limit = direct_streaming_limit(
        query,
        bound_parameters=parameters,
    )
    scans: list[ScanEstimate] = []
    for table in query.find_all(exp.Table):
        if table is metadata_count_table:
            continue
        managed = metadata.table(
            table.name,
            schema_name=table.db or None,
        )
        if managed is None:
            continue
        alias = (table.alias_or_name or table.name).lower()
        predicates = _table_predicates(query, table, alias)
        selectivity, partition_predicates = _selectivity(
            predicates,
            managed,
            parameters,
        )
        if streaming_limit is not None and table is streaming_limit.table:
            rows = streaming_limit.maximum_rows_read
            if managed.estimated_rows is not None:
                rows = min(rows, managed.estimated_rows)
            bytes_read = (
                (
                    0
                    if rows == 0
                    else max(
                        1,
                        round(
                            managed.file_size_bytes
                            * rows
                            / managed.estimated_rows
                        ),
                    )
                )
                if managed.file_size_bytes is not None
                and managed.estimated_rows is not None
                and managed.estimated_rows > 0
                else None
            )
        else:
            rows = (
                max(1, round(managed.estimated_rows * selectivity))
                if managed.estimated_rows is not None
                else None
            )
            bytes_read = (
                max(1, round(managed.file_size_bytes * selectivity))
                if managed.file_size_bytes is not None
                else None
            )
        scans.append(
            ScanEstimate(
                relation=managed.qualified_name,
                table_uuid=managed.table_uuid,
                estimated_rows_read=rows,
                estimated_bytes_read=bytes_read,
                partition_predicates=partition_predicates,
            )
        )
    return tuple(scans)


def _table_predicates(
    query: exp.Expression,
    table: exp.Table,
    alias: str,
) -> tuple[exp.Expression, ...]:
    owner = table.find_ancestor(exp.Select)
    if owner is None:
        return ()
    relation_count = sum(
        1
        for candidate in owner.find_all(exp.Table)
        if candidate.find_ancestor(exp.Select) is owner
    )
    predicates: list[exp.Expression] = []
    for where in owner.find_all(exp.Where):
        if where.find_ancestor(exp.Select) is not owner:
            continue
        for predicate in _conjuncts(where.this):
            columns = tuple(predicate.find_all(exp.Column))
            if columns and all(
                column.table.lower() == alias
                if column.table
                else relation_count == 1
                for column in columns
            ):
                predicates.append(predicate)
    return tuple(predicates)


def _selectivity(
    predicates: tuple[exp.Expression, ...],
    table: ManagedTableMetadata,
    parameters: dict[str, object],
) -> tuple[float, tuple[str, ...]]:
    partition_columns = {
        column.column_name.lower(): column
        for column in table.partition_columns
    }
    selectivity = 1.0
    recognized: list[str] = []
    for predicate in predicates:
        comparison = _column_comparison(predicate)
        if comparison is None:
            continue
        column, predicate_transform, operator, value = comparison
        partition = partition_columns.get(column.name.lower())
        if (
            partition is None
            or (
                predicate_transform is not None
                and predicate_transform != partition.transform
            )
            or not _known_value(value, parameters)
        ):
            continue
        recognized.append(predicate.sql(dialect="duckdb"))
        statistics = table.statistics_for(column.name)
        if isinstance(operator, (exp.EQ, exp.NullSafeEQ)):
            if statistics is not None and statistics.distinct_count:
                selectivity *= min(1.0, 1 / statistics.distinct_count)
            elif partition.transform == "bucket" and partition.bucket_count:
                selectivity *= 1 / partition.bucket_count
            elif table.file_count:
                selectivity *= min(1.0, 1 / table.file_count)
        elif isinstance(operator, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)):
            selectivity *= 0.25
    return max(0.0, min(1.0, selectivity)), tuple(recognized)


def _column_comparison(
    expression: exp.Expression,
) -> tuple[
    exp.Column,
    str | None,
    exp.Expression,
    exp.Expression,
] | None:
    if isinstance(expression, exp.Between):
        operand = _partition_operand(expression.this)
        if operand is not None:
            return (*operand, expression, expression.args["low"])
    if not isinstance(
        expression,
        (exp.EQ, exp.NullSafeEQ, exp.GT, exp.GTE, exp.LT, exp.LTE),
    ):
        return None
    left = _partition_operand(expression.this)
    if left is not None:
        return (*left, expression, expression.expression)
    right = _partition_operand(expression.expression)
    if right is not None:
        return (*right, expression, expression.this)
    return None


def _partition_operand(
    expression: exp.Expression,
) -> tuple[exp.Column, str | None] | None:
    if isinstance(expression, exp.Column):
        return expression, None
    transforms = {
        exp.Year: "year",
        exp.Month: "month",
        exp.Day: "day",
        exp.Hour: "hour",
    }
    for expression_type, transform in transforms.items():
        if isinstance(expression, expression_type) and isinstance(
            expression.this,
            exp.Column,
        ):
            return expression.this, transform
    return None


def _known_value(
    expression: exp.Expression,
    parameters: dict[str, object],
) -> bool:
    if isinstance(expression, (exp.Literal, exp.Boolean, exp.Null)):
        return True
    if isinstance(expression, exp.Placeholder):
        name = str(expression.this or "").lower()
        return name in parameters
    if isinstance(expression, (exp.Cast, exp.TryCast)):
        return _known_value(expression.this, parameters)
    return False


def _conjuncts(expression: exp.Expression) -> tuple[exp.Expression, ...]:
    if isinstance(expression, exp.Paren):
        return _conjuncts(expression.this)
    if isinstance(expression, exp.And):
        return (*_conjuncts(expression.this), *_conjuncts(expression.expression))
    return (expression,)


def _difference(
    authored: tuple[ScanEstimate, ...],
    executable: tuple[ScanEstimate, ...],
    attribute: str,
) -> int | None:
    authored_values = tuple(getattr(scan, attribute) for scan in authored)
    executable_values = tuple(getattr(scan, attribute) for scan in executable)
    if any(value is None for value in (*authored_values, *executable_values)):
        return None
    return max(0, sum(authored_values) - sum(executable_values))
