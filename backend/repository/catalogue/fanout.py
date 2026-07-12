"""Authoritative crawl-enrichment fan-out state in DuckLake."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from repository.catalogue.records import (
    CrawlMaterializationFanout,
    CrawlMaterializationFanoutMember,
)


class CrawlMaterializationFanoutStore:
    def __init__(self, catalogue) -> None:
        self.catalogue = catalogue

    def plan(
        self,
        crawl_id: UUID,
        *,
        members: list[CrawlMaterializationFanoutMember],
    ) -> CrawlMaterializationFanout:
        if any(member.crawl_id != crawl_id for member in members):
            raise ValueError("every fan-out member must belong to the planned crawl")
        identities = {
            (member.materialization_id, member.definition_revision_id)
            for member in members
        }
        if len(identities) != len(members):
            raise ValueError("crawl materialization fan-out contains duplicate members")
        triggered_count = len(members)
        existing = self.get(crawl_id)
        if existing is not None:
            existing_identities = {
                (member.materialization_id, member.definition_revision_id)
                for member in self.members(crawl_id)
            }
            if existing.triggered_count != triggered_count or existing_identities != identities:
                raise ValueError("crawl materialization fan-out is already planned differently")
            return existing
        now = datetime.now(UTC)
        completed_at = now if triggered_count == 0 else None
        with self.catalogue.lake.transaction():
            self.catalogue.connection.execute(
                f"INSERT INTO {self._table('crawl_materialization_fanouts')} VALUES "
                "(?, ?, ?, 0, 0, ?)",
                [crawl_id, now, triggered_count, completed_at],
            )
            if members:
                self.catalogue.connection.executemany(
                    f"INSERT INTO {self._table('crawl_materialization_fanout_members')} "
                    "VALUES (?, ?, ?, ?, ?, 'planned', NULL, NULL)",
                    [
                        [
                            member.crawl_id,
                            member.materialization_id,
                            member.definition_revision_id,
                            member.scope_kind,
                            member.scope_id,
                        ]
                        for member in members
                    ],
                )
            self.catalogue.set_commit_message(
                author="Atlas repository",
                message="Planned crawl materialization fan-out",
                extra={"crawl_id": str(crawl_id), "triggered_count": triggered_count},
            )
        return self.get_required(crawl_id)

    def refresh(self, crawl_id: UUID) -> CrawlMaterializationFanout:
        """Recompute settled counts from authoritative crawl-scope coverage."""

        fanout = self.get_required(crawl_id)
        rows = self.catalogue.connection.execute(
            f"SELECT m.materialization_id, m.definition_revision_id, r.status, r.error, "
            "r.completed_at FROM "
            f"{self._table('crawl_materialization_fanout_members')} AS m JOIN "
            f"{self._table('materialization_scope_results')} AS r "
            "ON r.materialization_id = m.materialization_id "
            "AND r.definition_revision_id = m.definition_revision_id "
            "AND r.scope_kind = m.scope_kind AND r.scope_id = m.scope_id "
            "WHERE m.crawl_id = ? AND r.status IN ('succeeded', 'failed')",
            [crawl_id],
        ).fetchall()
        counts = {"succeeded": 0, "failed": 0}
        with self.catalogue.lake.transaction():
            for materialization_id, revision_id, status, error, completed_at in rows:
                member_status = "failed" if status == "failed" else "settled"
                counts[str(status)] += 1
                self.catalogue.connection.execute(
                    f"UPDATE {self._table('crawl_materialization_fanout_members')} "
                    "SET status = ?, settled_at = ?, error = ? WHERE crawl_id = ? "
                    "AND materialization_id = ? AND definition_revision_id = ?",
                    [
                        member_status,
                        completed_at,
                        error if member_status == "failed" else None,
                        crawl_id,
                        materialization_id,
                        revision_id,
                    ],
                )
        settled = sum(counts.values())
        failed = counts.get("failed", 0)
        if settled > fanout.triggered_count:
            raise ValueError("crawl coverage exceeds its frozen materialization fan-out")
        completed_at = (
            fanout.completed_at or datetime.now(UTC)
            if settled == fanout.triggered_count
            else None
        )
        if (
            settled == fanout.settled_count
            and failed == fanout.failed_count
            and completed_at == fanout.completed_at
        ):
            return fanout
        with self.catalogue.lake.transaction():
            self.catalogue.connection.execute(
                f"UPDATE {self._table('crawl_materialization_fanouts')} "
                "SET settled_count = ?, failed_count = ?, completed_at = ? "
                "WHERE crawl_id = ?",
                [settled, failed, completed_at, crawl_id],
            )
            self.catalogue.set_commit_message(
                author="Atlas repository",
                message="Settled crawl materialization fan-out",
                extra={
                    "crawl_id": str(crawl_id),
                    "settled_count": settled,
                    "failed_count": failed,
                },
            )
        return self.get_required(crawl_id)

    def members(self, crawl_id: UUID) -> list[CrawlMaterializationFanoutMember]:
        cursor = self.catalogue.connection.execute(
            f"SELECT * FROM {self._table('crawl_materialization_fanout_members')} "
            "WHERE crawl_id = ? ORDER BY materialization_id",
            [crawl_id],
        )
        rows = cursor.fetchall()
        names = [item[0] for item in cursor.description]
        return [
            CrawlMaterializationFanoutMember.model_validate(
                dict(zip(names, row, strict=True))
            )
            for row in rows
        ]

    def crawls_for_scope(
        self,
        *,
        materialization_id: UUID,
        definition_revision_id: UUID,
        scope_kind: str,
        scope_id: str,
    ) -> list[UUID]:
        rows = self.catalogue.connection.execute(
            f"SELECT crawl_id FROM {self._table('crawl_materialization_fanout_members')} "
            "WHERE materialization_id = ? AND definition_revision_id = ? "
            "AND scope_kind = ? AND scope_id = ?",
            [materialization_id, definition_revision_id, scope_kind, scope_id],
        ).fetchall()
        return [UUID(str(row[0])) for row in rows]

    def get(self, crawl_id: UUID) -> CrawlMaterializationFanout | None:
        cursor = self.catalogue.connection.execute(
            f"SELECT * FROM {self._table('crawl_materialization_fanouts')} "
            "WHERE crawl_id = ? LIMIT 1",
            [crawl_id],
        )
        row = cursor.fetchone()
        if row is None:
            return None
        names = [item[0] for item in cursor.description]
        return CrawlMaterializationFanout.model_validate(dict(zip(names, row, strict=True)))

    def get_required(self, crawl_id: UUID) -> CrawlMaterializationFanout:
        value = self.get(crawl_id)
        if value is None:
            raise ValueError(f"crawl {crawl_id} has no planned materialization fan-out")
        return value

    def terminal(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[CrawlMaterializationFanout]:
        cursor = self.catalogue.connection.execute(
            f"SELECT * FROM {self._table('crawl_materialization_fanouts')} "
            "WHERE completed_at IS NOT NULL ORDER BY completed_at LIMIT ? OFFSET ?",
            [limit, offset],
        )
        rows = cursor.fetchall()
        names = [item[0] for item in cursor.description]
        return [
            CrawlMaterializationFanout.model_validate(dict(zip(names, row, strict=True)))
            for row in rows
        ]

    def _table(self, name: str) -> str:
        return ".".join(
            '"' + part.replace('"', '""') + '"'
            for part in (self.catalogue.config.alias, self.catalogue.config.schema, name)
        )
