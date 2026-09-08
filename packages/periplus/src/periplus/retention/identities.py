"""Exact-identity transaction fences shared by ingestion, materialization and retirement."""
from collections.abc import Iterable
from datetime import datetime

from periplus.platform.catalogue.exceptions import CatalogueConflictError

TABLE = "material._periplus_retention_identities"


class EvidenceRetired(CatalogueConflictError):
    """A delayed producer cannot resurrect an explicitly retired identity."""


def touch(catalogue, kind: str, identities: Iterable[str], *, create: bool = False) -> None:
    """Call inside the writer's transaction; first creation requires its ingestion lease.

    Updating a dedicated lifecycle row, rather than immutable evidence, makes a
    concurrent retirement conflict with the write even under snapshot isolation.
    """
    connection = catalogue.trusted_connection
    for identity in sorted(set(map(str, identities))):
        rows = connection.execute(f"SELECT retired_at FROM {TABLE} WHERE kind=? AND identity=?",
                                  [kind, identity]).fetchall()
        if len(rows) > 1:
            raise CatalogueConflictError("duplicate retention identity")
        if rows and rows[0][0] is not None:
            raise EvidenceRetired(f"{kind} identity has been retired")
        if not rows:
            if not create:
                raise CatalogueConflictError("missing retention identity")
            connection.execute(f"INSERT INTO {TABLE} VALUES (?, ?, 0, NULL)", [kind, identity])
        else:
            connection.execute(f"UPDATE {TABLE} SET revision=revision+1 WHERE kind=? AND identity=?",
                               [kind, identity])


def retire(catalogue, kind: str, identity: str, now: datetime) -> None:
    touch(catalogue, kind, [identity])
    catalogue.trusted_connection.execute(
        f"UPDATE {TABLE} SET retired_at=? WHERE kind=? AND identity=?", [now, kind, str(identity)])


def retired(catalogue, kind: str, identity: str) -> bool:
    return bool(catalogue.trusted_connection.execute(
        f"SELECT 1 FROM {TABLE} WHERE kind=? AND identity=? AND retired_at IS NOT NULL LIMIT 1",
        [kind, str(identity)]).fetchall())
