"""Small lifecycle identities and resumable object retirements, not corpus history."""
from periplus.platform.catalogue.physical.base import MATERIAL_SCHEMA, RelationName, TableLayout
from periplus.platform.catalogue.schema_types import ColumnDef

IDENTITIES = RelationName(MATERIAL_SCHEMA, "_periplus_retention_identities")
OBJECTS = RelationName(MATERIAL_SCHEMA, "_periplus_retention_objects")
TABLE_COLUMNS = {
    IDENTITIES: {
        "kind": ColumnDef("VARCHAR", nullable=False),
        "identity": ColumnDef("VARCHAR", nullable=False),
        "revision": ColumnDef("BIGINT", nullable=False),
        "retired_at": ColumnDef("TIMESTAMPTZ"),
    },
    OBJECTS: {
        "object_key": ColumnDef("VARCHAR", nullable=False),
        "content_sha256": ColumnDef("VARCHAR", nullable=False),
        "stored_bytes": ColumnDef("BIGINT", nullable=False),
        "retired_at": ColumnDef("TIMESTAMPTZ", nullable=False),
        "retired_snapshot": ColumnDef("BIGINT", nullable=False),
        "snapshots_cleared_at": ColumnDef("TIMESTAMPTZ"),
        "retirement_id": ColumnDef("UUID", nullable=False),
    },
}
TABLE_LAYOUTS = {relation: TableLayout() for relation in TABLE_COLUMNS}
TABLE_COMMENTS = {IDENTITIES: "Per-evidence lifecycle fences; compact retired identities suppress delayed replay.",
                  OBJECTS: "Objects awaiting snapshot-safe physical reclamation."}
COLUMN_COMMENTS = {
    IDENTITIES: {"kind": "Observation, collection, or content lifecycle identity.",
                 "identity": "Stable identifier, without source content or URL.",
                 "revision": "Transaction conflict fence for this exact identity.",
                 "retired_at": "Retirement time; null while retained."},
    OBJECTS: {"object_key": "Repository-relative object awaiting reclamation.",
              "content_sha256": "Logical content identity.", "stored_bytes": "Expected stored size.",
              "retired_at": "Time the last retained reference was removed.",
              "retired_snapshot": "Snapshot boundary that must expire before physical deletion.",
              "snapshots_cleared_at": "First confirmation that old snapshots expired; starts the raw-reader grace period.",
              "retirement_id": "Unique deletion episode; prevents stale work from changing a later retirement of the same key."},
}
