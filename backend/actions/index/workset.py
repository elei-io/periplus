"""Disk-backed exact frontier and result state for an index run."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

import duckdb


@dataclass(frozen=True, slots=True)
class FrontierItem:
    url: str
    depth: int


class IndexBudgetExceeded(RuntimeError):
    pass


IndexEdge = tuple[str, str, str, int, int, bool, bool, bool]


class IndexWorkset:
    def __init__(
        self,
        *,
        max_pages: int,
        max_links: int,
        max_temp_bytes: int,
        temp_directory: str | None = None,
    ) -> None:
        if max_pages <= 0 or max_links <= 0 or max_temp_bytes <= 0:
            raise ValueError("index budgets must be greater than zero")
        directory = (
            Path(temp_directory).expanduser()
            if temp_directory
            else Path(tempfile.gettempdir())
        )
        directory.mkdir(parents=True, exist_ok=True)
        _cleanup_stale_worksets(directory)
        handle = tempfile.NamedTemporaryFile(
            prefix="atlas-index-",
            suffix=".duckdb",
            dir=directory,
            delete=False,
        )
        handle.close()
        self.path = Path(handle.name)
        self.path.unlink()
        self.connection = duckdb.connect(str(self.path))
        self.max_pages = max_pages
        self.max_links = max_links
        self.max_temp_bytes = max_temp_bytes
        self._write_operations = 0
        self.connection.execute(
            """
            CREATE TABLE frontier (
                url VARCHAR PRIMARY KEY,
                depth INTEGER NOT NULL,
                state VARCHAR NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE edges (
                source_url VARCHAR NOT NULL,
                target_url VARCHAR NOT NULL,
                text VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                depth INTEGER NOT NULL,
                link_index INTEGER NOT NULL,
                internal BOOLEAN NOT NULL,
                included BOOLEAN NOT NULL,
                crawlable BOOLEAN NOT NULL,
                PRIMARY KEY (source_url, target_url, link_index)
            );
            """
        )

    def seed(self, url: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO frontier (url, depth) VALUES (?, 0)",
            [url],
        )

    def claim(self, limit: int) -> list[FrontierItem]:
        rows = self.connection.execute(
            "SELECT url, depth FROM frontier WHERE state = 'pending' "
            "ORDER BY depth, url LIMIT ?",
            [limit],
        ).fetchall()
        if rows:
            self.connection.executemany(
                "UPDATE frontier SET state = 'running' WHERE url = ?",
                [(str(url),) for url, _depth in rows],
            )
        return [FrontierItem(url=str(url), depth=int(depth)) for url, depth in rows]

    def complete_page(
        self,
        item: FrontierItem,
        *,
        edges: Iterable[IndexEdge],
        succeeded: bool,
    ) -> None:
        initial_disk_bytes = self._disk_bytes()
        estimated_write = 0
        edge_iterator = iter(edges)
        self.connection.execute("BEGIN TRANSACTION")
        try:
            for edge_batch in _chunks(edge_iterator, 4_096):
                estimated_write += sum(
                    96
                    + len(item.url.encode("utf-8"))
                    + len(target_url.encode("utf-8"))
                    + len(text.encode("utf-8"))
                    + len(title.encode("utf-8"))
                    + (48 + len(target_url.encode("utf-8")) if crawlable else 0)
                    for (
                        target_url,
                        text,
                        title,
                        _depth,
                        _link_index,
                        _internal,
                        _included,
                        crawlable,
                    ) in edge_batch
                )
                if initial_disk_bytes + estimated_write * 2 > self.max_temp_bytes:
                    raise IndexBudgetExceeded(
                        f"index would exceed its {self.max_temp_bytes} byte temporary-disk budget"
                    )
                self.connection.executemany(
                    "INSERT OR IGNORE INTO edges "
                    "(source_url, target_url, text, title, depth, link_index, internal, "
                    "included, crawlable) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item.url,
                            target_url,
                            text,
                            title,
                            depth,
                            link_index,
                            internal,
                            included,
                            crawlable,
                        )
                        for (
                            target_url,
                            text,
                            title,
                            depth,
                            link_index,
                            internal,
                            included,
                            crawlable,
                        ) in edge_batch
                    ],
                )
            if self.edge_count() > self.max_links:
                raise IndexBudgetExceeded(
                    f"index exceeded its {self.max_links} discovered-link budget"
                )
            self.connection.execute(
                "INSERT OR IGNORE INTO frontier (url, depth) "
                "SELECT DISTINCT target_url, depth + 1 FROM edges "
                "WHERE source_url = ? AND crawlable",
                [item.url],
            )
            if self.page_count() > self.max_pages:
                raise IndexBudgetExceeded(
                    f"index exceeded its {self.max_pages} page budget"
                )
            self.connection.execute(
                "UPDATE frontier SET state = ? WHERE url = ?",
                ["done" if succeeded else "failed", item.url],
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        self._write_operations += 1
        self._check_disk_budget(checkpoint=False)
        if self._write_operations % 128 == 0:
            self._check_disk_budget()

    def result_count(self, *, dedupe: bool) -> int:
        expression = "count(DISTINCT target_url)" if dedupe else "count(*)"
        return int(
            self.connection.execute(
                f"SELECT {expression} FROM edges WHERE included"
            ).fetchone()[0]
        )

    def edge_count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM edges").fetchone()[0])

    def page_count(self) -> int:
        return int(self.connection.execute("SELECT count(*) FROM frontier").fetchone()[0])

    def completed_page_count(self) -> int:
        return int(
            self.connection.execute(
                "SELECT count(*) FROM frontier WHERE state IN ('done', 'failed')"
            ).fetchone()[0]
        )

    def failed_page_count(self) -> int:
        return int(
            self.connection.execute(
                "SELECT count(*) FROM frontier WHERE state = 'failed'"
            ).fetchone()[0]
        )

    def close(self, *, check_budget: bool = True) -> None:
        try:
            if check_budget:
                self._check_disk_budget()
        finally:
            self.connection.close()
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> IndexWorkset:
        return self

    def __exit__(self, exc_type, *_args: object) -> None:
        self.close(check_budget=exc_type is None)

    def _check_disk_budget(self, *, checkpoint: bool = True) -> None:
        if checkpoint:
            self.connection.execute("CHECKPOINT")
        size = self._disk_bytes()
        if size > self.max_temp_bytes:
            raise IndexBudgetExceeded(
                f"index exceeded its {self.max_temp_bytes} byte temporary-disk budget"
            )

    def _disk_bytes(self) -> int:
        wal_path = Path(f"{self.path}.wal")
        return sum(
            path.stat().st_size
            for path in (self.path, wal_path)
            if path.exists()
        )


def _cleanup_stale_worksets(directory: Path) -> None:
    try:
        grace = float(os.getenv("ATLAS_INDEX_STALE_TEMP_AGE_SECONDS", "86400"))
    except ValueError:
        grace = 86400
    cutoff = time.time() - max(3600, grace)
    for path in directory.glob("atlas-index-*.duckdb"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


def _chunks(values: Iterator[IndexEdge], size: int) -> Iterator[list[IndexEdge]]:
    while chunk := list(islice(values, size)):
        yield chunk
