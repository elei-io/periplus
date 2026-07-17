"""Bounded Arrow/Parquet encoding for the Atlas DOM projection."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import pyarrow as pa
import pyarrow.parquet as pq

from dom.encoder import ElementRow, iter_html_elements

ELEMENT_ARROW_SCHEMA = pa.schema(
    [
        pa.field("document_id", pa.string(), nullable=False),
        pa.field("element_index", pa.int32(), nullable=False),
        pa.field("parent_index", pa.int32()),
        pa.field("subtree_end_index", pa.int32(), nullable=False),
        pa.field("depth", pa.int32(), nullable=False),
        pa.field("tag", pa.string(), nullable=False),
        pa.field("namespace_uri", pa.string()),
        pa.field("attributes", pa.map_(pa.string(), pa.string()), nullable=False),
        pa.field("text_direct", pa.string(), nullable=False),
        pa.field("text_tail", pa.string(), nullable=False),
    ]
)


@dataclass(frozen=True, slots=True)
class DomParquet:
    path: Path
    element_count: int
    size_bytes: int


class _BudgetedOutput:
    """Reject a Parquet write before it crosses its staging-byte budget."""

    def __init__(self, output: BinaryIO, max_bytes: int | None) -> None:
        self.output = output
        self.max_bytes = max_bytes
        self.bytes_written = 0

    def write(self, data: bytes) -> int:
        if self.max_bytes is not None and self.bytes_written + len(data) > self.max_bytes:
            raise ValueError(
                f"DOM projection exceeded its {self.max_bytes} byte staging budget"
            )
        written = self.output.write(data)
        self.bytes_written += written
        return written

    def tell(self) -> int:
        return self.output.tell()

    def flush(self) -> None:
        self.output.flush()

    def writable(self) -> bool:
        return True

    @property
    def closed(self) -> bool:
        return self.output.closed


def element_record_batches(
    rows: Iterable[ElementRow],
    *,
    document_id: str,
    batch_rows: int = 16_384,
) -> Iterator[pa.RecordBatch]:
    """Convert element rows into bounded Arrow batches."""

    if batch_rows <= 0:
        raise ValueError("batch_rows must be greater than zero")
    pending: list[dict[str, object]] = []
    for row in rows:
        pending.append(
            {
                "document_id": document_id,
                "element_index": row.element_index,
                "parent_index": row.parent_index,
                "subtree_end_index": row.subtree_end_index,
                "depth": row.depth,
                "tag": row.tag,
                "namespace_uri": row.namespace_uri,
                "attributes": row.attributes,
                "text_direct": row.text_direct,
                "text_tail": row.text_tail,
            }
        )
        if len(pending) >= batch_rows:
            yield pa.RecordBatch.from_pylist(pending, schema=ELEMENT_ARROW_SCHEMA)
            pending.clear()
    if pending:
        yield pa.RecordBatch.from_pylist(pending, schema=ELEMENT_ARROW_SCHEMA)


def write_dom_parquet(
    captured_html: str,
    *,
    document_id: str,
    path: Path,
    batch_rows: int = 16_384,
    max_rows: int | None = None,
    max_bytes: int | None = None,
) -> DomParquet:
    """Write one page projection without materializing all element rows."""

    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    if max_rows is not None and max_rows <= 0:
        raise ValueError("max_rows must be greater than zero")
    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")
    try:
        with path.open("wb") as raw_output:
            output = _BudgetedOutput(raw_output, max_bytes)
            with pq.ParquetWriter(
                output,
                ELEMENT_ARROW_SCHEMA,
                compression="zstd",
                use_dictionary=["tag", "namespace_uri"],
            ) as writer:
                for batch in element_record_batches(
                    iter_html_elements(captured_html),
                    document_id=document_id,
                    batch_rows=batch_rows,
                ):
                    count += batch.num_rows
                    if max_rows is not None and count > max_rows:
                        raise ValueError(
                            f"DOM projection exceeded its {max_rows} element budget"
                        )
                    writer.write_batch(batch)
        size_bytes = path.stat().st_size
        return DomParquet(
            path=path,
            element_count=count,
            size_bytes=size_bytes,
        )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
