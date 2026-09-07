"""Append-only relations for finite collection intent and shared observation lineage."""
from periplus.platform.catalogue.physical.base import INGEST_SCHEMA, RelationName, TableLayout
from periplus.platform.catalogue.schema_types import ColumnDef

COLLECTIONS = RelationName(INGEST_SCHEMA, "collections")
COLLECTION_OUTCOMES = RelationName(INGEST_SCHEMA, "collection_outcomes")
FULFILLMENTS = RelationName(INGEST_SCHEMA, "fulfillments")
ACQUISITION_REASONS = RelationName(INGEST_SCHEMA, "acquisition_reasons")

_COMMON = {
    "record_id": ColumnDef("UUID", nullable=False),
    "visibility": ColumnDef("VARCHAR", nullable=False),
    "recorded_at": ColumnDef("TIMESTAMPTZ", nullable=False),
}
TABLE_COLUMNS = {
    COLLECTIONS: _COMMON | {
        "collection_id": ColumnDef("UUID", nullable=False),
        "specification": ColumnDef("JSON", nullable=False),
    },
    COLLECTION_OUTCOMES: _COMMON | {
        "collection_id": ColumnDef("UUID", nullable=False),
        "outcome": ColumnDef("VARCHAR", nullable=False),
        "seed_provenance": ColumnDef("JSON"),
        "consumed_pages": ColumnDef("BIGINT", nullable=False),
        "supplied_pages": ColumnDef("BIGINT", nullable=False),
        "failed_pages": ColumnDef("BIGINT", nullable=False),
    },
    FULFILLMENTS: _COMMON | {
        "collection_id": ColumnDef("UUID", nullable=False),
        "observation_id": ColumnDef("UUID", nullable=False),
        "requested_url": ColumnDef("VARCHAR", nullable=False),
        "parent_observation_id": ColumnDef("UUID"),
        "depth": ColumnDef("INTEGER", nullable=False),
        "rule_id": ColumnDef("VARCHAR", nullable=False),
        "mode": ColumnDef("VARCHAR", nullable=False),
    },
    ACQUISITION_REASONS: _COMMON | {
        "observation_id": ColumnDef("UUID", nullable=False),
        "collection_id": ColumnDef("UUID"),
        "parent_observation_id": ColumnDef("UUID"),
        "reason": ColumnDef("VARCHAR", nullable=False),
        "selection_provenance": ColumnDef("JSON"),
        "policy_version": ColumnDef("VARCHAR", nullable=False),
        "rule_id": ColumnDef("VARCHAR", nullable=False),
    },
}
TABLE_LAYOUTS = {
    relation: TableLayout(partition_by=("month(recorded_at)",),
                          sort_by=("recorded_at ASC", "record_id ASC"))
    for relation in TABLE_COLUMNS
}
TABLE_COMMENTS = {
    COLLECTIONS: "Frozen collection requests, independent of physical acquisition.",
    COLLECTION_OUTCOMES: "Terminal collection outcomes and page accounting.",
    FULFILLMENTS: "One request URL supplied by one existing observation.",
    ACQUISITION_REASONS: "Causal request or background reasons frozen at dispatch.",
}
_DESCRIPTIONS = {
    "record_id": "Stable idempotency identity within this evidence relation.",
    "visibility": "Whether this evidence may be exposed publicly.",
    "recorded_at": "Time the represented decision was durably accepted.",
    "collection_id": "Finite collection request identity, when applicable.",
    "specification": "Frozen collection intent, SQL, scope, and limits.",
    "outcome": "Reason this collection settled.",
    "seed_provenance": "Frozen seed snapshot, query identity, selection time, and candidate digest.",
    "consumed_pages": "Page units consumed by dispatch or result reuse.",
    "supplied_pages": "Successfully supplied request URLs.",
    "failed_pages": "Request URLs with a terminal acquisition failure.",
    "observation_id": "Terminal observation supplying or caused by this decision.",
    "requested_url": "Normalized URL selected for this request.",
    "parent_observation_id": "Winning parent observation, when discovered from a page.",
    "depth": "First committed traversal depth for this request URL.",
    "rule_id": "Frozen selection rule identity.",
    "mode": "Whether the request acquired, shared, or reused this result.",
    "reason": "Whether collection intent or background policy caused dispatch.",
    "selection_provenance": "Frozen historical-check snapshot, query identity, and selection policy version.",
    "policy_version": "Effective policy identity at dispatch.",
}
COLUMN_COMMENTS = {
    relation: {name: _DESCRIPTIONS[name] for name in columns}
    for relation, columns in TABLE_COLUMNS.items()
}
