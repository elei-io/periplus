"""Authoritative crawl-enrichment fan-out state in DuckLake."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from repository.catalogue.records import (
    CrawlMaterializationFanout,
    CrawlMaterializationFanoutMember,
)


class MissingCrawlMaterializationFanout(ValueError):
    """A scope result arrived before its crawl fan-out plan became visible."""


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
        identities = self._member_identities(members)
        if len(identities) != len(members):
            raise ValueError("crawl materialization fan-out contains duplicate members")
        triggered_count = len(members)
        now = datetime.now(UTC)
        completed_at = now if triggered_count == 0 else None
        with self.catalogue.lake.transaction():
            existing = self.get(crawl_id)
            existing_members = self.members(crawl_id)
            existing_identities = self._member_identities(existing_members)
            if existing is not None:
                if (
                    existing.triggered_count != triggered_count
                    or existing_identities != identities
                ):
                    raise ValueError(
                        "crawl materialization fan-out is already planned differently"
                    )
                return existing
            if existing_members and existing_identities != identities:
                raise ValueError(
                    "crawl materialization fan-out has an incomplete interrupted plan"
                )
            self.catalogue.connection.execute(
                f"INSERT INTO {self._table('crawl_materialization_fanouts')} VALUES "
                "(?, ?, ?, 0, 0, ?)",
                [crawl_id, now, triggered_count, completed_at],
            )
            if members and not existing_members:
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
        """Read settlement derived from authoritative immutable scope coverage."""

        return self.get_required(crawl_id)

    def members(self, crawl_id: UUID) -> list[CrawlMaterializationFanoutMember]:
        cursor = self.catalogue.connection.execute(
            "SELECT m.crawl_id, m.materialization_id, m.definition_revision_id, "
            "m.scope_kind, m.scope_id, "
            "CASE WHEN r.status = 'failed' THEN 'failed' "
            "WHEN r.status = 'succeeded' THEN 'settled' ELSE 'planned' END AS status, "
            "r.completed_at AS settled_at, "
            "CASE WHEN r.status = 'failed' THEN r.error ELSE NULL END AS error FROM "
            f"{self._table('crawl_materialization_fanout_members')} AS m LEFT JOIN "
            f"{self._table('materialization_scope_results')} AS r "
            "ON r.materialization_id = m.materialization_id "
            "AND r.definition_revision_id = m.definition_revision_id "
            "AND r.scope_kind = m.scope_kind AND r.scope_id = m.scope_id "
            "WHERE m.crawl_id = ? ORDER BY m.materialization_id",
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
        crawl_filter = ""
        parameters: list[object] = [
            materialization_id,
            definition_revision_id,
            scope_kind,
            scope_id,
        ]
        if scope_kind == "crawl":
            crawl_filter = " AND m.crawl_id = ?"
            parameters.append(UUID(scope_id))
        rows = self.catalogue.connection.execute(
            f"SELECT m.crawl_id FROM "
            f"{self._table('crawl_materialization_fanout_members')} AS m JOIN "
            f"{self._table('crawl_materialization_fanouts')} AS f "
            "ON f.crawl_id = m.crawl_id "
            "WHERE m.materialization_id = ? AND m.definition_revision_id = ? "
            f"AND m.scope_kind = ? AND m.scope_id = ?{crawl_filter}",
            parameters,
        ).fetchall()
        return [UUID(str(row[0])) for row in rows]

    def get(self, crawl_id: UUID) -> CrawlMaterializationFanout | None:
        cursor = self.catalogue.connection.execute(
            "SELECT f.crawl_id, f.planning_completed_at, f.triggered_count, "
            "count(r.status) FILTER (WHERE r.status IN ('succeeded', 'failed')) "
            "AS settled_count, "
            "count(r.status) FILTER (WHERE r.status = 'failed') AS failed_count, "
            "CASE WHEN count(r.status) FILTER "
            "(WHERE r.status IN ('succeeded', 'failed')) = f.triggered_count "
            "THEN coalesce(max(r.completed_at), f.planning_completed_at) "
            "ELSE NULL END AS completed_at FROM "
            f"{self._table('crawl_materialization_fanouts')} AS f LEFT JOIN "
            f"{self._table('crawl_materialization_fanout_members')} AS m "
            "ON m.crawl_id = f.crawl_id LEFT JOIN "
            f"{self._table('materialization_scope_results')} AS r "
            "ON r.materialization_id = m.materialization_id "
            "AND r.definition_revision_id = m.definition_revision_id "
            "AND r.scope_kind = m.scope_kind AND r.scope_id = m.scope_id "
            "WHERE f.crawl_id = ? GROUP BY f.crawl_id, f.planning_completed_at, "
            "f.triggered_count LIMIT 1",
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
            raise MissingCrawlMaterializationFanout(
                f"crawl {crawl_id} has no planned materialization fan-out"
            )
        return value

    def _table(self, name: str) -> str:
        return ".".join(
            '"' + part.replace('"', '""') + '"'
            for part in (self.catalogue.config.alias, self.catalogue.config.schema, name)
        )

    @staticmethod
    def _member_identities(
        members: list[CrawlMaterializationFanoutMember],
    ) -> set[tuple[UUID, UUID, str, str]]:
        return {
            (
                member.materialization_id,
                member.definition_revision_id,
                member.scope_kind,
                member.scope_id,
            )
            for member in members
        }
