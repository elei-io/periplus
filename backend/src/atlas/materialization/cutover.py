"""Evidence-preserving checks for the append-only material cutover."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

from atlas.ingestion.objects.document import (
    ExactDocumentIdentity,
    ExactDocumentRepository,
)
from atlas.ingestion.objects.html import HtmlIdentity, RawHtmlRepository
from atlas.materialization.registry import PROJECTIONS, REGISTRY_DIGEST
from atlas.materialization.runtime import _drop_unregistered_material_relations
from atlas.platform.catalogue import Catalogue
from atlas.platform.catalogue.public import validate_public_catalogue


@dataclass(frozen=True, slots=True)
class CutoverReport:
    checked_at: str
    snapshot: int
    visit_count: int
    document_count: int
    latest_finished_at: str | None
    latest_visit_id: str | None
    verified_objects: int
    registry_digest: str


def preflight(
    catalogue: Catalogue,
    html_repository: RawHtmlRepository,
    document_repository: ExactDocumentRepository,
) -> CutoverReport:
    """Verify the complete authoritative snapshot before fencing old work."""

    snapshot = catalogue.latest_snapshot()
    if snapshot is None:
        raise RuntimeError("catalogue has no committed ingestion snapshot")
    visit_count, latest_finished_at, latest_visit_id = (
        catalogue.trusted_remote_rows(
            f"""
            SELECT count(*), max(finished_at),
                   arg_max(visit_id::VARCHAR, (finished_at, visit_id))
            FROM ingest.visits AT (VERSION => {snapshot})
            """
        )[0]
    )
    document_rows = catalogue.trusted_remote_rows(
        f"""
        SELECT document_id::VARCHAR, content_sha256, content_bytes,
               object_key, storage_encoding, stored_bytes
        FROM ingest.documents AT (VERSION => {snapshot})
        ORDER BY document_id
        """
    )
    dangling = int(
        catalogue.trusted_remote_rows(
            f"""
            WITH documents AS (
                SELECT *
                FROM ingest.documents AT (VERSION => {snapshot})
            ),
            visits AS (
                SELECT *
                FROM ingest.visits AT (VERSION => {snapshot})
            )
            SELECT count(*)
            FROM documents AS document
            LEFT JOIN visits AS visit
              USING (visit_id)
            WHERE visit.visit_id IS NULL
               OR visit.document_id != document.document_id
            """
        )[0][0]
    )
    if dangling:
        raise RuntimeError(
            f"preflight found {dangling} dangling document references"
        )
    for (
        document_id,
        content_sha256,
        content_bytes,
        object_key,
        storage_encoding,
        stored_bytes,
    ) in document_rows:
        if storage_encoding == "zstd":
            html_repository.verify(
                str(object_key),
                expected=HtmlIdentity(
                    sha256=str(content_sha256),
                    size_bytes=int(content_bytes),
                ),
            )
        elif storage_encoding == "identity":
            document_repository.verify(
                str(object_key),
                expected=ExactDocumentIdentity(
                    sha256=str(content_sha256),
                    size_bytes=int(content_bytes),
                ),
            )
        else:
            raise RuntimeError(
                f"document {document_id} has unsupported storage encoding "
                f"{storage_encoding!r}"
            )
        actual_stored_bytes = html_repository.store.size(str(object_key))
        if actual_stored_bytes != int(stored_bytes):
            raise RuntimeError(
                f"document {document_id} stored size differs from evidence"
            )
    return CutoverReport(
        checked_at=datetime.now(UTC).isoformat(),
        snapshot=int(snapshot),
        visit_count=int(visit_count),
        document_count=len(document_rows),
        latest_finished_at=(
            latest_finished_at.isoformat()
            if latest_finished_at is not None
            else None
        ),
        latest_visit_id=(
            str(latest_visit_id) if latest_visit_id is not None else None
        ),
        verified_objects=len(document_rows),
        registry_digest=REGISTRY_DIGEST,
    )


def verify_cutover(catalogue: Catalogue) -> dict[str, int]:
    """Validate active registry relations and the matching public contract."""

    state = catalogue.trusted_remote_rows(
        "SELECT registry_digest "
        "FROM material._atlas_materialization_state "
        "ORDER BY activated_at DESC LIMIT 1"
    )
    if state != [(REGISTRY_DIGEST,)]:
        raise RuntimeError("active material generation has the wrong registry")
    validate_public_catalogue(catalogue)
    counts: dict[str, int] = {}
    for spec in PROJECTIONS:
        counts[spec.name] = int(
            catalogue.trusted_remote_rows(
                f"SELECT count(*) FROM {spec.relation.qualified}"
            )[0][0]
        )
    catalogue.trusted_remote_rows("SELECT * FROM web.visit LIMIT 1")
    catalogue.trusted_remote_rows("SELECT * FROM web.page LIMIT 1")
    return counts


def finalize_cutover(catalogue: Catalogue) -> dict[str, int]:
    counts = verify_cutover(catalogue)
    _drop_unregistered_material_relations(catalogue)
    return counts


def write_report(report: CutoverReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
