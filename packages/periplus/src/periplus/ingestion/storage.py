"""Immutable ClickHouse evidence writes and exact identity reconciliation."""

from datetime import UTC, datetime
from hashlib import sha256
from importlib.resources import files
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.catalogue.lineage import AcquisitionReason, FulfillmentRecord
from periplus.platform.catalogue.records import VisitEvidence, canonical_json
from periplus.platform.clickhouse import ClickHouseClient
from periplus.retention.identities import write_claims

_TABLES = frozenset({"visits", "fulfillments", "acquisition_reasons"})


class EvidenceReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["visit", "fulfillment", "acquisition_reason"]
    identity: UUID
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ingested_at: AwareDatetime
    created: bool


def evidence_digest(evidence: VisitEvidence | FulfillmentRecord | AcquisitionReason) -> str:
    return sha256(canonical_json(evidence.model_dump(mode="json")).encode()).hexdigest()


def visit_row(evidence: VisitEvidence) -> dict[str, JsonValue]:
    """Preserve the frozen package in one row; nested parents are implicit."""
    row = evidence.visit.model_dump(mode="json")
    row["capture_policy"] = canonical_json(row["capture_policy"])
    document = evidence.document.model_dump(mode="json") if evidence.document else {}
    for column in ("representation", "declared_media_type", "detected_media_type", "charset",
                   "content_sha256", "content_bytes", "object_key", "storage_encoding", "stored_bytes"):
        row[column] = document.get(column)
    row["document_attempt_id"] = document.get("attempt_id")
    row["document_observed_at"] = document.get("observed_at")
    children = []
    for attempt in evidence.attempts:
        child = attempt.model_dump(mode="json", exclude={"visit_id"})
        if child["resource_usage"] is not None:
            child["resource_usage"] = canonical_json(child["resource_usage"])
        child["steps"] = [
            {**step.model_dump(mode="json", exclude={"attempt_id", "parameters"}),
             "parameters": canonical_json(step.parameters)}
            for step in evidence.steps if step.attempt_id == attempt.attempt_id
        ]
        children.append(child)
    row["attempts"] = children
    row["evidence_sha256"] = evidence_digest(evidence)
    return row


def install_ingestion_schema(client: ClickHouseClient) -> None:
    """Setup-only installation; the SQL file owns physical types and indexes."""
    source = files("periplus.ingestion").joinpath("schema.sql").read_text()
    for statement in source.split(";"):
        if statement.strip():
            client.execute(statement)


class EvidenceStore:
    def __init__(self, client: ClickHouseClient) -> None:
        self.client = client

    def validate(self) -> None:
        """Workers verify required tables; schema installation belongs to setup."""
        for table in sorted(_TABLES):
            self.client.execute(f"SELECT evidence_sha256, ingested_at FROM ingest.{table} LIMIT 0")

    def receipt(self, kind: Literal["visit", "fulfillment", "acquisition_reason"],
                identity: UUID, expected_digest: str) -> EvidenceReceipt | None:
        table = {"visit": "visits", "fulfillment": "fulfillments",
                 "acquisition_reason": "acquisition_reasons"}[kind]
        key = "visit_id" if kind == "visit" else "record_id"
        result = self.client.query(
            f"SELECT lower(hex(evidence_sha256)) AS digest, ingested_at FROM ingest.{table} "
            f"WHERE {key} = {{identity:UUID}} LIMIT 2", parameters={"identity": str(identity)},
        )["data"]
        if not result:
            return None
        if len(result) != 1 or result[0]["digest"] != expected_digest:
            raise CatalogueConflictError("immutable evidence identity has different or duplicate durable rows")
        return EvidenceReceipt(kind=kind, identity=identity, evidence_sha256=expected_digest,
                               ingested_at=datetime.fromisoformat(result[0]["ingested_at"]).replace(tzinfo=UTC),
                               created=False)

    def record_visit(self, evidence: VisitEvidence) -> EvidenceReceipt:
        digest = evidence_digest(evidence)
        claims = {"observation": [str(evidence.visit.visit_id)],
                  "content": [evidence.document.content_sha256] if evidence.document else []}
        with write_claims(claims):
            existing = self.receipt("visit", evidence.visit.visit_id, digest)
            if existing:
                return existing
            self._insert("visits", visit_row(evidence))
            receipt = self.receipt("visit", evidence.visit.visit_id, digest)
            if receipt is None:
                raise RuntimeError("acknowledged visit is not visible on its write route")
            return receipt.model_copy(update={"created": True})

    def record_lineage(self, evidence: FulfillmentRecord | AcquisitionReason) -> EvidenceReceipt:
        kind = evidence.kind
        table = "fulfillments" if kind == "fulfillment" else "acquisition_reasons"
        digest = evidence_digest(evidence)
        with write_claims({"observation": [str(evidence.observation_id)],
                           "collection": [str(evidence.collection_id)]}):
            existing = self.receipt(kind, evidence.record_id, digest)
            if existing:
                return existing
            row = evidence.model_dump(mode="json", exclude={"kind"})
            row["evidence_sha256"] = digest
            self._insert(table, row)
            receipt = self.receipt(kind, evidence.record_id, digest)
            if receipt is None:
                raise RuntimeError("acknowledged lineage is not visible on its write route")
            return receipt.model_copy(update={"created": True})

    def _insert(self, table: str, row: dict[str, JsonValue]) -> None:
        if table not in _TABLES:
            raise ValueError("unknown evidence table")
        self.client.insert_json(f"ingest.{table}", row)
