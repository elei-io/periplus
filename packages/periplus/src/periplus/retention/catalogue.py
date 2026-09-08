"""Bounded logical retirement; registered files remain LakeDucktor's responsibility."""
from datetime import datetime, timedelta
from collections.abc import Callable
from threading import Timer
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from periplus.materialization.registry import PROJECTIONS
from periplus.platform.catalogue.client import Catalogue
from periplus.ingestion.objects.store import ObjectStore
from periplus.retention.identities import write_claims, retire
from periplus.retention import store as retirement_store
from periplus.retention.policy import EXPIRED_SQL


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)
    observation_id: UUID
    finished_at: datetime
    content_sha256: str | None
    object_key: str | None
    stored_bytes: int = 0


class RetentionPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    as_of: datetime
    candidates: list[Candidate]
    referenced_object_bytes: int
    # Not a physical-reclamation estimate: shared references and snapshots remain.
    bytes_semantics: str = "candidate_document_sizes_not_reclaimable_storage"


class RetentionCatalogue:
    def __init__(self, catalogue: Catalogue):
        self.catalogue = catalogue
        self.connection = catalogue.trusted_connection

    def plan(self, *, now: datetime, limit: int = 50, minimum_age_seconds: int = 86400,
             after: tuple[datetime, UUID] | None = None) -> RetentionPlan:
        if not 1 <= limit <= 100 or minimum_age_seconds < 3600 or now.utcoffset() is None:
            raise ValueError("invalid retention planning bounds")
        timer = Timer(10, self.connection.interrupt)
        timer.daemon = True
        timer.start()
        try:
            anchor = "AND (v.finished_at, v.visit_id) > (?, ?)" if after else ""
            parameters = [now - timedelta(seconds=minimum_age_seconds), now]
            if after:
                parameters.extend(after)
            parameters.append(limit)
            rows = self.connection.execute(f"""
                SELECT v.visit_id, v.finished_at, d.content_sha256, d.object_key, coalesce(d.stored_bytes, 0)
                FROM ingest.visits v LEFT JOIN ingest.documents d ON d.document_id=v.document_id
                WHERE v.finished_at <= ? AND NOT EXISTS (
                    SELECT 1 FROM ingest.fulfillments f
                    LEFT JOIN ingest.collections c ON c.collection_id=f.collection_id
                    LEFT JOIN ingest.collection_outcomes o ON o.collection_id=c.collection_id
                    WHERE f.observation_id=v.visit_id AND NOT coalesce({EXPIRED_SQL}, false)
                ) {anchor}
                ORDER BY v.finished_at, v.visit_id LIMIT ?
            """, parameters).fetchall()
            candidates = [Candidate(observation_id=row[0], finished_at=row[1], content_sha256=row[2],
                                    object_key=row[3], stored_bytes=row[4]) for row in rows]
            return RetentionPlan(as_of=now, candidates=candidates,
                                 referenced_object_bytes=sum(item.stored_bytes for item in candidates))
        finally:
            timer.cancel()
            timer.join()

    def purge_observation(self, candidate: Candidate, *, now: datetime) -> bool:
        """Caller must first exclude the bounded current frontier ownership roots."""
        identity = str(candidate.observation_id)
        with write_claims({"observation": [identity],
                           "content": [candidate.content_sha256] if candidate.content_sha256 else []},
                          allow_retired=True), self.catalogue.transaction():
            rows = self.connection.execute("SELECT document_id FROM ingest.visits WHERE visit_id=?", [identity]).fetchall()
            if not rows:
                return False
            protected = self.connection.execute(f"""
                SELECT 1 FROM ingest.fulfillments f
                LEFT JOIN ingest.collections c ON c.collection_id=f.collection_id
                LEFT JOIN ingest.collection_outcomes o ON o.collection_id=c.collection_id
                WHERE f.observation_id=? AND NOT coalesce({EXPIRED_SQL}, false) LIMIT 1
            """, [identity, now]).fetchall()
            if protected:
                return False
            documents = self.connection.execute(
                "SELECT content_sha256, object_key, stored_bytes FROM ingest.documents WHERE visit_id=?", [identity]).fetchall()
            retire("observation", identity, now)
            self._delete_projections("visit", "visit_id", identity)
            self.connection.execute("DELETE FROM ingest.steps WHERE attempt_id IN (SELECT attempt_id FROM ingest.attempts WHERE visit_id=?)", [identity])
            for table, column in (("attempts", "visit_id"), ("documents", "visit_id"),
                                  ("acquisition_reasons", "observation_id"), ("fulfillments", "observation_id"), ("visits", "visit_id")):
                self.connection.execute(f"DELETE FROM ingest.{table} WHERE {column}=?", [identity])
            for content_hash, key, size in documents:
                if not self.connection.execute("SELECT 1 FROM ingest.documents WHERE content_sha256=? LIMIT 1", [content_hash]).fetchall():
                    self._delete_projections("content", "content_sha256", content_hash)
                if self.connection.execute("SELECT 1 FROM ingest.documents WHERE object_key=? LIMIT 1", [key]).fetchall():
                    continue
                retirement_store.enqueue(content_hash, key, size, now)
        return True

    def _delete_projections(self, grain: str, column: str, identity: str) -> None:
        tables = {row[0] for row in self.connection.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name=? AND schema_name='material'",
            [self.catalogue.config.alias]).fetchall()}
        for spec in PROJECTIONS:
            if spec.ownership_grain != grain:
                continue
            if column not in spec.physical_columns:
                raise RuntimeError(f"projection {spec.name} lacks a retention ownership key")
            for table in tables:
                if table == spec.name or table.startswith(f"_periplus_rebuild_{spec.name}_") or table.startswith(f"_periplus_retired_{spec.name}_"):
                    quoted = '"' + table.replace('"', '""') + '"'
                    self.connection.execute(f"DELETE FROM material.{quoted} WHERE {column}=?", [identity])

    def expired_requests(self, *, now: datetime, limit: int = 50, after: UUID | None = None) -> list[UUID]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid request retirement bound")
        rows = self.connection.execute(f"""
            SELECT c.collection_id FROM ingest.collections c
            JOIN ingest.collection_outcomes o USING (collection_id)
            WHERE {EXPIRED_SQL} AND (? IS NULL OR c.collection_id > ?) ORDER BY c.collection_id LIMIT ?
        """, [now, after, after, limit]).fetchall()
        return [UUID(str(row[0])) for row in rows]

    def purge_request(self, identity: UUID, *, now: datetime, limit: int = 100) -> bool:
        if not 1 <= limit <= 100:
            raise ValueError("invalid request retirement bound")
        with write_claims({"collection": [str(identity)]}, allow_retired=True), self.catalogue.transaction():
            if not self.connection.execute(f"""SELECT 1 FROM ingest.collections c
                JOIN ingest.collection_outcomes o USING(collection_id)
                WHERE c.collection_id=? AND {EXPIRED_SQL}""", [str(identity), now]).fetchall():
                return False
            retire("collection", str(identity), now)
            self.connection.execute("""DELETE FROM ingest.fulfillments WHERE record_id IN
                (SELECT record_id FROM ingest.fulfillments WHERE collection_id=? ORDER BY record_id LIMIT ?)""", [str(identity), limit])
            if self.connection.execute("SELECT 1 FROM ingest.fulfillments WHERE collection_id=? LIMIT 1", [str(identity)]).fetchall():
                return False
            self.connection.execute("DELETE FROM ingest.collection_outcomes WHERE collection_id=?", [str(identity)])
            self.connection.execute("DELETE FROM ingest.collections WHERE collection_id=?", [str(identity)])
            return True

    def reclaim_objects(self, store: ObjectStore, *, now: datetime, grace_seconds: int = 86400, limit: int = 50, content_hashes: tuple[str, ...], check_ownership: Callable[[], None]) -> int:
        """Remove only raw objects. Never delete registered Parquet or expire snapshots."""
        with write_claims({"content": content_hashes}):
            return self._reclaim_objects(store, now=now, grace_seconds=grace_seconds,
                limit=limit, content_hashes=content_hashes, check_ownership=check_ownership)

    def _reclaim_objects(self, store, *, now, grace_seconds, limit, content_hashes, check_ownership):
        from periplus.ingestion.objects.publication import begin_reclamation, end_reclamation
        if grace_seconds < 3600 or not 1 <= limit <= 100:
            raise ValueError("invalid physical reclamation bounds")
        # A crash after logical deletion leaves -1. Establish a conservative,
        # committed upper snapshot bound before that receipt can authorize GC.
        # Capture the pending keys first; concurrent retirements must not inherit
        # a snapshot boundary from before their own commit.
        pending = retirement_store.pending_snapshots(content_hashes, limit)
        latest = self.catalogue.latest_snapshot()
        if latest is None:
            return 0
        retirement_store.anchor_snapshots(pending, latest)
        alias = '"' + self.catalogue.config.alias.replace('"', '""') + '"'
        oldest = self.connection.execute(f"SELECT min(snapshot_id) FROM {alias}.snapshots()").fetchone()[0]
        if oldest is None:
            return 0
        retirement_store.mark_snapshots_cleared(content_hashes, oldest, now, limit)
        rows = retirement_store.reclaimable(content_hashes, oldest, now, grace_seconds, limit)
        removed = 0
        for key, content_hash, retirement_id in rows:
            if content_hash not in content_hashes:
                continue
            check_ownership()
            if not begin_reclamation(store, content_hash, now=now):
                continue
            try:
                referenced = self.connection.execute(
                    "SELECT 1 FROM ingest.documents WHERE object_key=? LIMIT 1", [key]).fetchall()
                if referenced:
                    retirement_store.remove(retirement_id)
                    continue
                check_ownership()
                store.delete(key)
                retirement_store.remove(retirement_id)
                removed += 1
            finally:
                check_ownership()
                end_reclamation(store, content_hash)
        return removed
