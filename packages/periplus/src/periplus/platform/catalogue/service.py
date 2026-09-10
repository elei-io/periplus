"""Typed ingestion operations for Periplus-owned DuckLake evidence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import json
from typing import Any
from uuid import UUID

import pyarrow as pa

from periplus.platform.catalogue.client import Catalogue
from periplus.retention.identities import write_claims
from periplus.platform.catalogue.exceptions import (
    CatalogueConflictError,
    CatalogueValidationError,
)
from periplus.platform.catalogue.records import (
    AttemptRecord,
    DocumentRecord,
    IngestionWriteResult,
    StepRecord,
    VisitEvidence,
    VisitRecord,
    canonical_json,
)
from periplus.platform.catalogue.schema import (
    ATTEMPTS,
    DOCUMENTS,
    STEPS,
    VISITS,
    RelationName,
    expected_columns,
)

from periplus.platform.catalogue.lineage import LINEAGE_ADAPTER, LineageEvidence
from periplus.platform.catalogue.physical.lineage import (
    COLLECTIONS, COLLECTION_OUTCOMES, FULFILLMENTS, ACQUISITION_REASONS,
)

_LINEAGE_RELATIONS = {
    "collection": COLLECTIONS, "collection_outcome": COLLECTION_OUTCOMES,
    "fulfillment": FULFILLMENTS, "acquisition_reason": ACQUISITION_REASONS,
}


class CatalogueService:
    """The only durable write boundary for immutable ingestion evidence."""

    def __init__(self, catalogue: Catalogue) -> None:
        self.catalogue = catalogue

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
        with write_claims({
            "observation": [str(entry.visit.visit_id) for entry in unique],
            "content": [entry.document.content_sha256 for entry in unique if entry.document],
        }, wait_seconds=0), self.catalogue.transaction():
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
                json_columns=("resource_usage",),
            )
            self._append(
                STEPS,
                [
                    _step_values(step)
                    for entry in missing
                    for step in entry.steps
                ],
                json_columns=("parameters",),
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
                json_columns=("capture_policy",),
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

    def get_lineage(self, kind: str, identity: UUID) -> LineageEvidence | None:
        relation = _LINEAGE_RELATIONS[kind]
        rows = self._rows_by_ids(relation, "record_id", [identity])
        return LINEAGE_ADAPTER.validate_python(rows[0] | {"kind": kind}) if rows else None

    def record_lineage(self, entries: Sequence[LineageEvidence]) -> list[IngestionWriteResult]:
        if not entries:
            return []
        unique = _unique_records(entries, identity=lambda value: (value.kind, value.record_id))
        created = {}
        with write_claims({
            "collection": [str(entry.collection_id) for entry in unique if getattr(entry, "collection_id", None)],
            "observation": [str(entry.observation_id) for entry in unique if getattr(entry, "observation_id", None)],
        }, wait_seconds=0), self.catalogue.transaction():
            for kind, relation in _LINEAGE_RELATIONS.items():
                group = [entry for entry in unique if entry.kind == kind]
                durable = {
                    row["record_id"]: LINEAGE_ADAPTER.validate_python(row | {"kind": kind})
                    for row in self._rows_by_ids(relation, "record_id", [entry.record_id for entry in group])
                }
                # Backends may expose UUID columns as strings or UUID values.
                durable = {str(key): value for key, value in durable.items()}
                missing = []
                json_columns = tuple(name for name, column in expected_columns()[relation].items() if column.data_type == "JSON")
                for entry in group:
                    existing = durable.get(str(entry.record_id))
                    if existing is not None and existing != entry:
                        raise CatalogueConflictError(f"{kind} {entry.record_id} has different durable evidence")
                    created[(kind, entry.record_id)] = existing is None
                    if existing is None:
                        values = {key: str(value) if isinstance(value, UUID) else value
                                  for key, value in entry.model_dump(exclude={"kind"}).items()}
                        encoded = entry.model_dump(mode="json")
                        values.update({key: encoded[key] for key in json_columns})
                        missing.append(values)
                self._append(relation, missing, json_columns=json_columns)
        snapshot = self._result_snapshot(changed=any(created.values()))
        return [IngestionWriteResult(kind="lineage", identity=entry.record_id,
                                     created=created[(entry.kind, entry.record_id)],
                                     repository_snapshot=snapshot) for entry in entries]

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
        return [
            _decode_json_columns(
                relation,
                dict(zip(names, row, strict=True)),
            )
            for row in rows
        ]

    def _append(
        self,
        relation: RelationName,
        rows: list[dict[str, object]],
        *,
        json_columns: tuple[str, ...] = (),
    ) -> None:
        if not rows:
            return
        encoded = [
            {
                key: (
                    canonical_json(value) if key in json_columns and value is not None else value
                )
                for key, value in row.items()
            }
            for row in rows
        ]
        self.catalogue.append_arrow(
            relation.table,
            pa.Table.from_pylist(encoded),
            schema_name=relation.schema,
            json_columns=frozenset(json_columns),
        )

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


def _visit_values(record: VisitRecord) -> dict[str, object]:
    values = record.model_dump(mode="python")
    values["capture_policy"] = record.capture_policy.model_dump(mode="json") if record.capture_policy else None
    return values


def _attempt_values(record: AttemptRecord) -> dict[str, object]:
    values = record.model_dump(mode="python")
    values["resource_usage"] = record.resource_usage.model_dump(mode="json") if record.resource_usage else None
    return values


def _step_values(record: StepRecord) -> dict[str, object]:
    return record.model_dump(mode="python")


def _decode_json_columns(
    relation: RelationName,
    values: dict[str, Any],
) -> dict[str, Any]:
    columns = expected_columns()[relation]
    return {
        name: (
            json.loads(value)
            if columns[name].data_type == "JSON"
            and isinstance(value, str)
            else value
        )
        for name, value in values.items()
    }


def _document_values(record: DocumentRecord) -> dict[str, object]:
    return record.model_dump(mode="python")


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
