"""Bounded document preparation; only Arrow files cross the parser boundary."""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Iterator
from concurrent.futures import (
    FIRST_COMPLETED,
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    wait,
)
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.compute as pc

from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.materialization.document_projection import (
    DocumentProjectionSource,
    VisitBatchContext,
)
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.metrics import preparation_attempt, step
from periplus.materialization.registry import BY_NAME, PROJECTIONS, ProjectionSpec
from periplus.platform.config.environment import get_int

# These bound in-flight source bytes, not the expanded DOM or Arrow allocation.
# Oversized documents run alone; documents beyond the hard limit fail explicitly.
PREFETCH_BYTES = 16 * 1024**2
DOCUMENT_BYTES = 128 * 1024**2
DOCUMENT_OUTPUT_BYTES = 256 * 1024**2
BATCH_OUTPUT_BYTES = 8 * 1024**3


@dataclass(frozen=True)
class ProjectionFiles:
    directories: tuple[Path, ...]

    @property
    def size_bytes(self) -> int:
        return sum(path.stat().st_size for directory in self.directories for path in directory.glob("*.arrow"))

    def dictionary_inputs(self) -> dict[str, pa.Table]:
        result = {}
        for spec in PROJECTIONS:
            if spec.dictionary_key is None:
                continue
            keys: set[str] = set()
            for directory in self.directories:
                table = _read(directory, spec.name)
                keys.update(table[spec.dictionary_key].to_pylist())
            result[spec.name] = pa.table(
                {spec.dictionary_key: sorted(keys)}, schema=spec.input_schema
            )
        return result

    def rows(
        self, spec: ProjectionSpec, dictionaries: dict[str, dict[str, int]]
    ) -> pa.Table:
        tables = []
        for directory in self.directories:
            table = _read(directory, spec.name)
            for dependency in spec.dictionary_dependencies:
                dictionary = BY_NAME[dependency]
                keys = _read(directory, dependency)[
                    dictionary.dictionary_key
                ].to_pylist()
                ids = pa.array(
                    [dictionaries[dependency][key] for key in keys], type=pa.int64()
                )
                column = table.schema.get_field_index(dictionary.dictionary_id)
                table = table.set_column(
                    column,
                    table.schema.field(column),
                    pc.take(ids, table.column(column)),
                )
            tables.append(table)
        return (
            pa.concat_tables(tables)
            if tables
            else pa.Table.from_batches([], schema=spec.input_schema)
        )


def _read(directory: Path, name: str) -> pa.Table:
    with pa.ipc.open_file(str(directory / f"{name}.arrow")) as reader:
        return reader.read_all()


def _initialize_parser() -> None:
    # A spawned parser owns no catalogue connection. JSON-LD validation uses its
    # own small, single-threaded DuckDB rather than inheriting the writer budget.
    os.environ["PERIPLUS_DUCKDB_THREADS"] = "1"
    os.environ["PERIPLUS_DUCKDB_MEMORY_LIMIT"] = "128MB"
    os.environ["PERIPLUS_DUCKDB_MAX_TEMP_DIRECTORY_SIZE"] = "0B"
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)


def _project(context: VisitBatchContext, directory: Path) -> int:
    directory.mkdir()
    dictionaries = {}
    outputs = {}
    for spec in PROJECTIONS:
        if spec.dictionary_key is not None:
            output = spec.rows(context)
            outputs[spec.name] = output
            dictionaries[spec.name] = {
                key: index
                for index, key in enumerate(output[spec.dictionary_key].to_pylist())
            }
    context = replace(context, dictionary_ids=dictionaries)
    size = 0
    for spec in PROJECTIONS:
        output = (
            outputs[spec.name]
            if spec.dictionary_key is not None
            else spec.rows(context)
        )
        if size + output.nbytes > DOCUMENT_OUTPUT_BYTES:
            raise ValueError("document projection exceeds 256 MiB Arrow output budget")
        path = directory / f"{spec.name}.arrow"
        with (
            pa.OSFile(str(path), "wb") as sink,
            pa.ipc.new_file(sink, output.schema) as writer,
        ):
            writer.write_table(output)
        size += path.stat().st_size
        if size > DOCUMENT_OUTPUT_BYTES:
            raise ValueError("document projection exceeds 256 MiB Arrow output budget")
        del output
    return size


def _parse(html: str | bytes, context: VisitBatchContext, directory: Path) -> int:
    source = context.sources[0]
    nodes, elements = parse_document(html)
    return _project(
        replace(
            context,
            parsed_nodes_by_content={source.content_sha256: nodes},
            parsed_elements_by_content={source.content_sha256: elements},
        ),
        directory,
    )


def _read_and_project(
    repository: RawHtmlRepository,
    source: DocumentProjectionSource,
    context: VisitBatchContext,
    directory: Path,
    pool: ProcessPoolExecutor,
) -> int:
    with step("html_read_decode"):
        if source.storage_encoding == "zstd":
            chunks = repository.iter_bytes(source.object_key)
        elif source.storage_encoding == "identity":
            chunks = ExactDocumentRepository(repository.store).iter_bytes(
                source.object_key
            )
        else:
            raise ValueError(
                f"unsupported HTML storage encoding {source.storage_encoding!r}"
            )
        data = bytearray()
        for chunk in chunks:
            if (
                len(data) + len(chunk) > source.content_bytes
                or len(data) + len(chunk) > DOCUMENT_BYTES
            ):
                raise ValueError(
                    "HTML exceeds declared content size or 128 MiB document budget"
                )
            data.extend(chunk)
        if len(data) != source.content_bytes:
            raise ValueError("HTML size differs from declared content size")
        # Canonical captured HTML must retain the old UTF-8 string parse path;
        # exact response HTML retains its byte/encoding-aware parse path.
        html = (
            data.decode("utf-8") if source.storage_encoding == "zstd" else bytes(data)
        )
        del data
    with step("html_parse"):
        return pool.submit(_parse, html, context, directory).result()


@contextmanager
def prepare_projections(
    repository: RawHtmlRepository,
    sources: tuple[DocumentProjectionSource, ...],
    *,
    visits: tuple[tuple[object, ...], ...],
    documents: tuple[tuple[object, ...], ...],
    content_output_hashes: frozenset[str],
    processes: int | None = None,
) -> Iterator[ProjectionFiles]:
    """Prepare every registry projection without retaining batch-wide DOM graphs.

    Each visit has at most one captured document. Assign its metadata to that
    document's context, and project remaining visits once in an empty context.
    All result files are ephemeral and removed on success, failure or cancellation.
    """
    preparation_attempt()
    processes = (
        get_int("PERIPLUS_MATERIALIZER_PARSER_PROCESSES", minimum=1)
        if processes is None
        else processes
    )
    if not 1 <= processes <= 8:
        raise ValueError("parser processes must be between 1 and 8")
    slots = min(8, processes * 2)
    with TemporaryDirectory(prefix="periplus-projections-") as temporary:
        root = Path(temporary)
        contexts = []
        assigned: set[str] = set()
        for source in sources:
            if not 0 <= source.content_bytes <= DOCUMENT_BYTES:
                raise ValueError("HTML exceeds 128 MiB document budget")
            source_visits = {
                observation.visit_id for observation in source.observations
            }
            if source_visits & assigned:
                raise ValueError("visit belongs to multiple HTML sources")
            assigned.update(source_visits)
            contexts.append(
                VisitBatchContext(
                    visits=tuple(row for row in visits if str(row[0]) in source_visits),
                    documents=tuple(
                        row for row in documents if str(row[2]) == source.content_sha256
                    ),
                    sources=(source,),
                    parsed_elements_by_content={},
                    parsed_nodes_by_content={},
                    observations_by_content={
                        source.content_sha256: source.observations
                    },
                    content_output_hashes=frozenset({source.content_sha256})
                    & content_output_hashes,
                )
            )
        remaining = VisitBatchContext(
            visits=tuple(row for row in visits if str(row[0]) not in assigned),
            documents=tuple(row for row in documents if str(row[1]) not in assigned),
            sources=(),
            parsed_elements_by_content={},
            parsed_nodes_by_content={},
            observations_by_content={},
            content_output_hashes=frozenset(),
        )
        directories = tuple(root / str(index) for index in range(len(sources) + 1))
        total_output = _project(remaining, directories[-1])
        if sources:
            pool = ProcessPoolExecutor(
                max_workers=processes,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_initialize_parser,
            )
            readers = ThreadPoolExecutor(
                max_workers=slots, thread_name_prefix="html-prefetch"
            )
            pending = {}
            index = 0
            reserved = 0
            try:
                while pending or index < len(sources):
                    while index < len(sources) and len(pending) < slots:
                        source = sources[index]
                        size = max(1, source.content_bytes)
                        if pending and reserved + size > PREFETCH_BYTES:
                            break
                        future = readers.submit(
                            _read_and_project,
                            repository,
                            source,
                            contexts[index],
                            directories[index],
                            pool,
                        )
                        pending[future] = size
                        reserved += size
                        index += 1
                    completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in completed:
                        reserved -= pending.pop(future)
                        total_output += future.result()
                        if (
                            total_output
                            > BATCH_OUTPUT_BYTES - slots * DOCUMENT_OUTPUT_BYTES
                        ):
                            raise ValueError(
                                "batch projection exceeds 8 GiB temporary Arrow budget"
                            )
            except BaseException:
                pool.terminate_workers()
                raise
            finally:
                readers.shutdown(wait=True, cancel_futures=True)
                pool.shutdown(wait=True, cancel_futures=True)
        yield ProjectionFiles(directories)
