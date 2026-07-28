"""Typed ingestion operations for Atlas-owned DuckLake evidence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

import pyarrow as pa

from atlas.platform.catalogue.client import Catalogue
from atlas.platform.catalogue.exceptions import (
    CatalogueConflictError,
    CatalogueValidationError,
)
from atlas.platform.catalogue.records import (
    AttemptRecord,
    CrawlRecord,
    DocumentRecord,
    IngestionWriteResult,
    StepRecord,
    VisitEvidence,
    VisitRecord,
    canonical_json,
)
from atlas.platform.catalogue.schema import (
    ATTEMPTS,
    CRAWLS,
    DOCUMENTS,
    STEPS,
    VISITS,
    RelationName,
    expected_columns,
)


class CatalogueService:
    """The only durable write boundary for immutable ingestion evidence."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

    def record_crawls(
        self,
        records: Sequence[CrawlRecord],
    ) -> list[IngestionWriteResult]:
        if not records:
            return []
        unique = _unique_records(records, identity=lambda value: value.crawl_id)
        created: dict[UUID, bool] = {}
        with self.catalogue.transaction():
            existing = self.get_crawls([record.crawl_id for record in unique])
            missing: list[CrawlRecord] = []
            for record in unique:
                durable = existing.get(record.crawl_id)
                if durable is None:
                    missing.append(record)
                    created[record.crawl_id] = True
                elif durable != record:
                    raise CatalogueConflictError(
                        f"crawl_id {record.crawl_id} has different durable evidence"
                    )
                else:
                    created[record.crawl_id] = False
            self._append(
                CRAWLS,
                [_crawl_values(record) for record in missing],
                variant_columns=("graph_config",),
            )
        snapshot = self._result_snapshot(changed=bool(missing))
        return [
            IngestionWriteResult(
                kind="crawl",
                identity=record.crawl_id,
                created=created[record.crawl_id],
                repository_snapshot=snapshot,
            )
            for record in records
        ]

    def record_visits(
        self,
        entries: Sequence[VisitEvidence],
    ) -> list[IngestionWriteResult]:
        if not entries:
            return []
        unique = _unique_records(
            entries,
            identity=lambda value: value.visit.visit_id,
        )
        created: dict[UUID, bool] = {}
        with self.catalogue.transaction():
            existing = self.get_visit_evidence(
                [entry.visit.visit_id for entry in unique]
            )
            missing: list[VisitEvidence] = []
            for entry in unique:
                durable = existing.get(entry.visit.visit_id)
                if durable is None:
                    missing.append(entry)
                    created[entry.visit.visit_id] = True
                elif durable != entry:
                    raise CatalogueConflictError(
                        f"visit_id {entry.visit.visit_id} has different durable evidence"
                    )
                else:
                    created[entry.visit.visit_id] = False

            self._append(
                ATTEMPTS,
                [
                    _attempt_values(attempt)
                    for entry in missing
                    for attempt in entry.attempts
                ],
            )
            self._append(
                STEPS,
                [
                    _step_values(step)
                    for entry in missing
                    for step in entry.steps
                ],
                variant_columns=("parameters",),
            )
            self._append(
                DOCUMENTS,
                [
                    _document_values(entry.document)
                    for entry in missing
                    if entry.document is not None
                ],
            )
            self._append(
                VISITS,
                [_visit_values(entry.visit) for entry in missing],
            )
        snapshot = self._result_snapshot(changed=bool(missing))
        return [
            IngestionWriteResult(
                kind="visit",
                identity=entry.visit.visit_id,
                created=created[entry.visit.visit_id],
                repository_snapshot=snapshot,
            )
            for entry in entries
        ]

    def get_crawl(self, crawl_id: UUID) -> CrawlRecord | None:
        return self.get_crawls([crawl_id]).get(crawl_id)

    def get_crawls(self, crawl_ids: Sequence[UUID]) -> dict[UUID, CrawlRecord]:
        rows = self._rows_by_ids(CRAWLS, "crawl_id", crawl_ids)
        return {
            record.crawl_id: record
            for row in rows
            if (record := CrawlRecord.model_validate(row))
        }

    def get_visit(self, visit_id: UUID) -> VisitRecord | None:
        rows = self._rows_by_ids(VISITS, "visit_id", [visit_id])
        return VisitRecord.model_validate(rows[0]) if rows else None

    def get_document(self, document_id: UUID) -> DocumentRecord | None:
        rows = self._rows_by_ids(DOCUMENTS, "document_id", [document_id])
        return DocumentRecord.model_validate(rows[0]) if rows else None

    def get_visit_evidence(
        self,
        visit_ids: Sequence[UUID],
    ) -> dict[UUID, VisitEvidence]:
        visits = {
            record.visit_id: record
            for row in self._rows_by_ids(VISITS, "visit_id", visit_ids)
            if (record := VisitRecord.model_validate(row))
        }
        if not visits:
            return {}
        attempts_by_visit: dict[UUID, list[AttemptRecord]] = {
            visit_id: [] for visit_id in visits
        }
        for row in self._rows_by_ids(ATTEMPTS, "visit_id", list(visits)):
            attempt = AttemptRecord.model_validate(row)
            attempts_by_visit[attempt.visit_id].append(attempt)
        for attempts in attempts_by_visit.values():
            attempts.sort(key=lambda value: value.attempt_index)

        attempt_visit = {
            attempt.attempt_id: visit_id
            for visit_id, attempts in attempts_by_visit.items()
            for attempt in attempts
        }
        steps_by_visit: dict[UUID, list[StepRecord]] = {
            visit_id: [] for visit_id in visits
        }
        if attempt_visit:
            for row in self._rows_by_ids(
                STEPS,
                "attempt_id",
                list(attempt_visit),
            ):
                step = StepRecord.model_validate(row)
                steps_by_visit[attempt_visit[step.attempt_id]].append(step)
        attempt_index = {
            attempt.attempt_id: attempt.attempt_index
            for attempts in attempts_by_visit.values()
            for attempt in attempts
        }
        for steps in steps_by_visit.values():
            steps.sort(
                key=lambda value: (
                    attempt_index[value.attempt_id],
                    value.step_index,
                )
            )

        documents = {
            record.visit_id: record
            for row in self._rows_by_ids(DOCUMENTS, "visit_id", list(visits))
            if (record := DocumentRecord.model_validate(row))
        }
        return {
            visit_id: VisitEvidence(
                visit=visit,
                attempts=tuple(attempts_by_visit[visit_id]),
                steps=tuple(steps_by_visit[visit_id]),
                document=documents.get(visit_id),
            )
            for visit_id, visit in visits.items()
        }

    def _rows_by_ids(
        self,
        relation: RelationName,
        column: str,
        identities: Sequence[UUID],
    ) -> list[dict[str, Any]]:
        values = list(dict.fromkeys(identities))
        if not values:
            return []
        sql_values = ", ".join(_sql_literal(str(value)) for value in values)
        rows = self.catalogue.trusted_remote_rows(
            f"SELECT * FROM {self._table(relation)} "
            f"WHERE {_quote_identifier(column)} IN ({sql_values})"
        )
        names = tuple(expected_columns()[relation])
        return [dict(zip(names, row, strict=True)) for row in rows]

    def _append(
        self,
        relation: RelationName,
        rows: list[dict[str, object]],
        *,
        variant_columns: tuple[str, ...] = (),
    ) -> None:
        if not rows:
            return
        if not variant_columns:
            self.catalogue.append(
                relation.table,
                rows,
                schema_name=relation.schema,
            )
            return
        encoded = [
            {
                key: (
                    canonical_json(value) if key in variant_columns else value
                )
                for key, value in row.items()
            }
            for row in rows
        ]
        registration = f"_atlas_upload_{id(rows):x}"
        connection = self.catalogue.trusted_connection
        connection.register(registration, pa.Table.from_pylist(encoded))
        try:
            projections = ", ".join(
                (
                    f"{_quote_identifier(column)}::JSON::VARIANT "
                    f"AS {_quote_identifier(column)}"
                    if column in variant_columns
                    else _quote_identifier(column)
                )
                for column in rows[0]
            )
            connection.execute(
                f"INSERT INTO {self._table(relation)} BY NAME "
                f"SELECT {projections} FROM {_quote_identifier(registration)}"
            )
        finally:
            connection.unregister(registration)

    def _result_snapshot(self, *, changed: bool) -> int:
        snapshot = (
            self.catalogue.last_committed_snapshot()
            if changed
            else self.catalogue.latest_snapshot()
        )
        if snapshot is None:
            raise CatalogueValidationError(
                "DuckLake did not publish a repository snapshot"
            )
        return snapshot

    def _table(self, relation: RelationName) -> str:
        return ".".join(
            _quote_identifier(value)
            for value in (
                relation.schema,
                relation.table,
            )
        )


def _unique_records(records: Sequence, *, identity) -> list:
    unique: dict[object, object] = {}
    for record in records:
        key = identity(record)
        previous = unique.get(key)
        if previous is not None and previous != record:
            raise CatalogueConflictError(
                f"batch contains conflicting evidence for {key}"
            )
        unique[key] = record
    return list(unique.values())


def _crawl_values(record: CrawlRecord) -> dict[str, object]:
    return record.model_dump(mode="python")


def _visit_values(record: VisitRecord) -> dict[str, object]:
    values = record.model_dump(mode="python")
    provenance = values["provenance"]
    values["provenance"] = {
        "kind": provenance["kind"],
        "system": provenance.get("system"),
        "dataset": provenance.get("dataset"),
        "source_record_id": provenance.get("source_record_id"),
    }
    return values


def _attempt_values(record: AttemptRecord) -> dict[str, object]:
    return record.model_dump(mode="python")


def _step_values(record: StepRecord) -> dict[str, object]:
    return record.model_dump(mode="python")


def _document_values(record: DocumentRecord) -> dict[str, object]:
    return record.model_dump(mode="python")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
