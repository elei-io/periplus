"""Disposable, disk-backed capture lookup derived solely from journal batches.

There is no remote index write or additional archive authority. Each process-owned
object-store handle shares a bounded-memory SQLite cache. Losing it requires
metadata replay; workers reading explicit archive references do not need it.
"""

import sqlite3
from threading import RLock
from uuid import UUID
from weakref import WeakKeyDictionary


class LookupIndex:
    def __init__(self) -> None:
        # SQLite owns a temporary on-disk database; it can spill beyond the
        # bounded pager cache without leaving a named recovery artifact behind.
        self.database = sqlite3.connect(
            "", check_same_thread=False, isolation_level=None
        )
        self.database.execute("PRAGMA temp_store=FILE")
        self.database.execute("PRAGMA cache_size=-8192")
        self.database.execute("PRAGMA journal_mode=MEMORY")
        self.database.execute("PRAGMA synchronous=OFF")
        self.database.execute(
            "CREATE TABLE captures (id BLOB PRIMARY KEY, digest BLOB NOT NULL, "
            "sequence INTEGER NOT NULL, segment INTEGER NOT NULL, offset INTEGER NOT NULL, "
            "retirement_sequence INTEGER, retirement_segment INTEGER, retirement_offset INTEGER) WITHOUT ROWID"
        )
        self.cursors = [(0, 0) for _ in range(16)]
        self.shards = [RLock() for _ in range(16)]
        self.lock = RLock()

    def lookup(self, identity: UUID, *, retirement: bool = False):
        columns = (
            "retirement_sequence,retirement_segment,retirement_offset"
            if retirement
            else "sequence,segment,offset"
        )
        with self.lock:
            row = self.database.execute(
                f"SELECT digest,{columns} FROM captures WHERE id=?", (identity.bytes,)
            ).fetchone()
        return row if row and row[1] is not None else None

    def apply(self, batch) -> None:
        # Validate a complete segment before advancing its disposable checkpoint.
        with self.lock:
            previous, sequence = self.cursors[batch.shard]
            if (batch.segment, batch.start) != (previous + 1, sequence + 1):
                raise ValueError("Archive index encountered a journal gap")
            self.database.execute("BEGIN")
            try:
                for offset, record in enumerate(batch.records):
                    capture = record.capture
                    digest = bytes.fromhex(capture.digest)
                    existing = self.lookup(capture.capture_id)
                    if existing and existing[0] != digest:
                        raise ValueError("Conflicting capture facts in archive journal")
                    if record.kind == "capture":
                        self.database.execute(
                            "INSERT OR IGNORE INTO captures(id,digest,sequence,segment,offset) VALUES(?,?,?,?,?)",
                            (
                                capture.capture_id.bytes,
                                digest,
                                batch.start + offset,
                                batch.segment,
                                offset,
                            ),
                        )
                    else:
                        if not existing:
                            raise ValueError("Retirement precedes archived capture")
                        self.database.execute(
                            "UPDATE captures SET retirement_sequence=?,retirement_segment=?,retirement_offset=? WHERE id=?",
                            (
                                batch.start + offset,
                                batch.segment,
                                offset,
                                capture.capture_id.bytes,
                            ),
                        )
                self.database.execute("COMMIT")
            except BaseException:
                self.database.execute("ROLLBACK")
                raise
            self.cursors[batch.shard] = (batch.segment, batch.end)

    def __del__(self) -> None:
        if hasattr(self, "database"):
            self.database.close()


_indexes: WeakKeyDictionary = WeakKeyDictionary()
_lock = RLock()


def index_for(store) -> LookupIndex:
    with _lock:
        if store not in _indexes:
            _indexes[store] = LookupIndex()
        return _indexes[store]
