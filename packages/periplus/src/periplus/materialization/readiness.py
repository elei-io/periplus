"""Read active-generation membership without consulting delivery or another cursor."""
from datetime import UTC, datetime
from threading import Timer
from uuid import UUID

from pydantic import BaseModel

from periplus.materialization import state
from periplus.materialization.registry import CONTENT_PRESENCE_PROJECTION, REGISTRY_DIGEST
from periplus.platform.catalogue.connection import _identifier


class ObservationReadiness(BaseModel):
    observation_id: UUID
    query_ready: bool | None
    reason: str
    generation_id: UUID | None = None
    as_of: datetime


def observation_readiness(catalogue, identities: list[UUID]) -> dict[UUID, ObservationReadiness]:
    if len(identities) > 100:
        raise ValueError("readiness reads allow at most 100 observations")
    identities = list(dict.fromkeys(identities))
    if not identities:
        return {}
    now = datetime.now(UTC)
    result = {identity: ObservationReadiness(observation_id=identity, query_ready=None,
        reason="readiness_projection_not_installed", as_of=now) for identity in identities}
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    presence = CONTENT_PRESENCE_PROJECTION
    if presence is None:
        return {identity: value.model_copy(update={"reason": "content_presence_projection_unavailable"})
                for identity, value in result.items()}
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        tables = connection.execute("""SELECT table_name FROM duckdb_tables()
            WHERE database_name = ? AND schema_name = 'material'
              AND table_name IN ('visit_readiness', ?)""",
            [catalogue.config.alias, presence.name]).fetchall()
        if {row[0] for row in tables} != {'visit_readiness', presence.name}:
            return result
        generation = state.readable_generation()
        if generation is None:
            return {identity: value.model_copy(update={"reason": "active_generation_unavailable"})
                    for identity, value in result.items()}
        values = ', '.join('(?::UUID)' for _ in identities)
        # Postgres publication state is checked on both sides of one lake read.
        # An activation in progress never yields a readiness proof.
        rows = connection.execute(f"""
            WITH wanted(id) AS (VALUES {values}),
            active AS (SELECT ?::UUID AS generation_id, ?::VARCHAR AS registry_digest)
            SELECT wanted.id, visit.visit_id, active.generation_id, active.registry_digest, proof.visit_id,
                   visit.document_id, document.document_id, document.detected_media_type,
                   content.content_sha256
            FROM wanted
            LEFT JOIN {alias}.ingest.visits visit ON visit.visit_id = wanted.id
            LEFT JOIN active ON true
            LEFT JOIN {alias}.material.visit_readiness proof ON proof.visit_id = visit.visit_id
            LEFT JOIN {alias}.ingest.documents document ON document.document_id = visit.document_id
              AND document.visit_id = visit.visit_id
            LEFT JOIN (SELECT content_sha256 FROM {alias}.material.{_identifier(presence.name)}
                       WHERE {presence.content_presence_predicate}) content
              ON content.content_sha256 = document.content_sha256
            LIMIT 201
        """, [*identities, generation.id, generation.registry_digest]).fetchmany(201)
        if state.readable_generation() != generation:
            return {identity: value.model_copy(update={"reason": "active_generation_changed"})
                    for identity, value in result.items()}
        if len(rows) != len(identities) or len({row[0] for row in rows}) != len(identities):
            return {identity: value.model_copy(update={"reason": "readiness_state_inconsistent"})
                    for identity, value in result.items()}
        for identity, visit_id, generation_id, digest, proof_id, expected_document, document_id, media_type, content_hash in rows:
            ready = None
            if visit_id is None:
                reason = "observation_commit_not_verified"
            elif generation_id is None:
                reason = "active_generation_unavailable"
            elif digest != REGISTRY_DIGEST:
                reason = "active_generation_registry_mismatch"
            elif expected_document is not None and document_id is None:
                reason = "document_commit_not_verified"
            else:
                # Another visit batch may own this HTML's shared DOM/JSON-LD
                # output. Its root marker must be present in this same snapshot.
                ready = proof_id is not None and (
                    media_type is None or media_type.lower() != 'text/html' or content_hash is not None
                )
                reason = "active_generation_committed" if ready else "materialization_pending"
            result[identity] = ObservationReadiness(observation_id=identity, query_ready=ready,
                reason=reason, generation_id=generation_id if visit_id is not None else None, as_of=now)
        return result
    finally:
        timer.cancel()
        timer.join()


class CollectionReadiness(BaseModel):
    collection_id: UUID
    query_ready: bool | None
    reason: str
    generation_id: UUID | None = None
    as_of: datetime


def collection_readiness(catalogue, identities: list[UUID]) -> dict[UUID, CollectionReadiness]:
    """Prove complete request results in one snapshot; never infer from a page preview."""
    if len(identities) > 100:
        raise ValueError("readiness reads allow at most 100 collections")
    identities = list(dict.fromkeys(identities))
    if not identities:
        return {}
    now = datetime.now(UTC)
    result = {identity: CollectionReadiness(collection_id=identity, query_ready=None,
        reason="readiness_projection_not_installed", as_of=now) for identity in identities}
    connection = catalogue.trusted_connection
    alias = _identifier(catalogue.config.alias)
    presence = CONTENT_PRESENCE_PROJECTION
    if presence is None:
        return result
    timer = Timer(10, connection.interrupt)
    timer.daemon = True
    timer.start()
    try:
        tables = connection.execute("""SELECT table_name FROM duckdb_tables()
            WHERE database_name = ? AND schema_name = 'material'
              AND table_name IN ('visit_readiness', ?)""",
            [catalogue.config.alias, presence.name]).fetchall()
        if {row[0] for row in tables} != {'visit_readiness', presence.name}:
            return result
        generation = state.readable_generation()
        if generation is None:
            return {identity: value.model_copy(update={"reason": "active_generation_unavailable"})
                    for identity, value in result.items()}
        values = ', '.join('(?::UUID)' for _ in identities)
        # Proof rows share one lake snapshot. Recheck Postgres publication after
        # reading, so a generation change is reported as unknown rather than ready.
        rows = connection.execute(f"""
            WITH wanted(id) AS (VALUES {values}),
            definitions AS (
                SELECT d.collection_id, count(*) AS n,
                       bool_and(json_extract_string(d.specification, '$.request_class') IN ('public', 'system', 'admin')) AS valid
                FROM {alias}.ingest.collections d JOIN wanted w ON w.id = d.collection_id
                GROUP BY d.collection_id
            ), outcomes AS (
                SELECT o.collection_id, count(*) AS n, first(o.supplied_pages) AS supplied,
                       first(o.failed_pages) AS failed
                FROM {alias}.ingest.collection_outcomes o JOIN definitions d
                  ON d.collection_id = o.collection_id
                GROUP BY o.collection_id
            ), results AS (
                SELECT f.collection_id, count(*) AS n, count(DISTINCT f.record_id) AS unique_records,
                       count(DISTINCT f.observation_id) AS unique_observations,
                       count(*) FILTER (WHERE v.visit_id IS NULL) AS missing_visits,
                       count(*) FILTER (WHERE v.outcome = 'succeeded') AS succeeded,
                       count(*) FILTER (WHERE v.outcome = 'failed') AS failed,
                       count(*) FILTER (WHERE v.document_id IS NOT NULL AND doc.document_id IS NULL) AS missing_documents,
                       count(*) FILTER (WHERE proof.visit_id IS NULL OR
                          (lower(doc.detected_media_type) = 'text/html' AND content.content_sha256 IS NULL)) AS pending
                FROM {alias}.ingest.fulfillments f JOIN definitions d
                  ON d.collection_id = f.collection_id
                LEFT JOIN {alias}.ingest.visits v ON v.visit_id = f.observation_id
                LEFT JOIN {alias}.ingest.documents doc ON doc.document_id = v.document_id AND doc.visit_id = v.visit_id
                LEFT JOIN {alias}.material.visit_readiness proof ON proof.visit_id = v.visit_id
                LEFT JOIN (SELECT content_sha256 FROM {alias}.material.{_identifier(presence.name)}
                           WHERE {presence.content_presence_predicate}) content ON content.content_sha256 = doc.content_sha256
                GROUP BY f.collection_id
            ), active AS (
                SELECT 1 AS n, ?::UUID AS generation_id, ?::VARCHAR AS digest
            )
            SELECT w.id, d.n, d.valid, o.n, o.supplied, o.failed,
                   coalesce(r.n, 0), coalesce(r.unique_records, 0), coalesce(r.unique_observations, 0),
                   coalesce(r.missing_visits, 0), coalesce(r.succeeded, 0), coalesce(r.failed, 0),
                   coalesce(r.missing_documents, 0), coalesce(r.pending, 0), a.n, a.generation_id, a.digest
            FROM wanted w LEFT JOIN definitions d ON d.collection_id = w.id
            LEFT JOIN outcomes o ON o.collection_id = w.id LEFT JOIN results r ON r.collection_id = w.id
            CROSS JOIN active a
        """, [*identities, generation.id, generation.registry_digest]).fetchmany(101)
        if state.readable_generation() != generation:
            return {identity: value.model_copy(update={"reason": "active_generation_changed"})
                    for identity, value in result.items()}
        if len(rows) != len(identities):
            raise ValueError("collection readiness result cardinality is inconsistent")
        for (identity, definitions, valid, outcomes, supplied, failed, total, unique_records,
             unique_observations, missing_visits, successes, failures, missing_documents, pending,
             active_count, generation, digest) in rows:
            ready = None
            if definitions is None:
                reason = "collection_commit_not_verified"
            elif definitions != 1 or not valid or (outcomes is not None and outcomes != 1):
                reason = "collection_readiness_state_inconsistent"
            elif outcomes is None:
                reason = "collection_outcome_not_verified"
            elif total != unique_records or total != unique_observations:
                reason = "collection_readiness_state_inconsistent"
            elif supplied is None or failed is None or supplied < 0 or failed < 0:
                reason = "collection_readiness_state_inconsistent"
            elif total != supplied + failed:
                reason = "collection_fulfillments_not_verified"
            elif missing_visits:
                reason = "observation_commit_not_verified"
            elif successes != supplied or failures != failed:
                reason = "collection_readiness_state_inconsistent"
            elif active_count != 1 or generation is None:
                reason = "active_generation_unavailable"
            elif digest != REGISTRY_DIGEST:
                reason = "active_generation_registry_mismatch"
            elif missing_documents:
                reason = "document_commit_not_verified"
            else:
                ready = pending == 0
                reason = "active_generation_committed" if ready else "materialization_pending"
            result[identity] = CollectionReadiness(collection_id=identity, query_ready=ready,
                reason=reason, generation_id=generation if definitions == 1 else None, as_of=now)
        return result
    finally:
        timer.cancel()
        timer.join()
