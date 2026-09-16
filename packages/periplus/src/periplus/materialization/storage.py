"""Deterministic material output from archived captures; no operational evidence."""

from hashlib import sha256
from importlib.resources import files
import re
from types import SimpleNamespace
from uuid import UUID

from periplus.ingestion.archive import Archive
from periplus.ingestion.captures import Capture, canonical
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.dom.links import links_from_elements
from periplus.materialization.html_content import html_content
from periplus.materialization.element_rows import insert_elements, insert_json_ld
from periplus.urls import normalize_url
from periplus.platform.clickhouse import ClickHouseClient
from periplus.platform.clickhouse.client import EncodedRow, INSERT_TARGET_BYTES
from periplus.retention.identities import write_claims

MAX_INPUT_BYTES = 96 * 1024 * 1024
MAX_OUTPUT_BYTES = 127 * 1024 * 1024
BATCH_INPUT_BYTES = 8 * 1024 * 1024


class MaterialInputError(ValueError):
    def __init__(self, identity: UUID, cause: Exception):
        super().__init__(f"capture {identity}: {type(cause).__name__}: {cause}")


def material_database(value: str) -> str:
    if not re.fullmatch(r"material(?:_[0-9a-f]{32})?", value):
        raise ValueError("Invalid material target")
    return value


def install_material_schema(
    client: ClickHouseClient, database: str = "material"
) -> None:
    source = files("periplus.materialization").joinpath("schema.sql").read_text()
    for statement in re.sub(r"\bmaterial\b", material_database(database), source).split(
        ";"
    ):
        if statement.strip():
            client.execute(statement)


def output_row(value: dict) -> EncodedRow:
    encoded = canonical(value)
    if len(encoded) > MAX_OUTPUT_BYTES:
        identity = value.get("document_id", value.get("capture_id", "unknown"))
        raise ValueError(f"Material row {identity} is {len(encoded)} bytes; limit is {MAX_OUTPUT_BYTES} bytes")
    digest = sha256(encoded).hexdigest()
    suffix = (b',' if value else b'') + b'"output_digest":"' + digest.encode() + b'"}\n'
    return EncodedRow({**value, "output_digest": digest}, encoded[:-1] + suffix)


def row_bytes(row) -> int:
    return len(row.wire) - 1 if isinstance(row, EncodedRow) else len(canonical(row))


def capture_row(capture: Capture, document: dict | None, links: list, archive_record_key: str) -> dict:
    payload, source = capture.payload, capture.source
    return output_row(
        dict(
            capture_id=str(capture.capture_id),
            evidence_digest=capture.digest,
            requested_url=capture.requested_url,
            url=normalize_url(capture.effective_url or capture.requested_url),
            effective_url=capture.effective_url,
            captured_at=capture.captured_at.isoformat()
            if capture.captured_at
            else None,
            timestamp_precision=capture.timestamp_precision,
            http_status=capture.http_status,
            completeness=capture.completeness,
            content_id=payload.content_id if payload else None,
            document_id=document["document_id"] if document else None,
            byte_length=payload.byte_length if payload else None,
            representation=payload.representation if payload else None,
            media_type=payload.media_type if payload else None,
            encoding=payload.charset if payload else None,
            object_key=payload.object_key if payload else None,
            storage_encoding=payload.storage_encoding if payload else None,
            stored_bytes=payload.stored_bytes if payload else None,
            source_provider=source.provider if source else "periplus",
            source_dataset=source.dataset if source else None,
            source_record_id=source.record_id if source else None,
            archive_record_key=archive_record_key,
            links=links,
        )
    )


class MaterialStore:
    def __init__(self, client: ClickHouseClient, database: str = "material"):
        self.client, self.database = client, material_database(database)

    def validate(self) -> None:
        for table in ("captures", "html_documents", "html_elements", "json_ld"):
            self.client.execute(
                f"SELECT output_digest FROM {self.database}.{table} LIMIT 0"
            )

    def digests(self, table: str, identities: list[str]) -> dict[str, str]:
        if not identities:
            return {}
        if table not in ("captures", "html_documents"):
            raise ValueError("Unknown material relation")
        key = "capture_id" if table == "captures" else "document_id"
        predicates = [
            f"{{id{i}:UUID}}" if table == "captures" else f"{{id{i}:String}}"
            for i in range(len(identities))
        ]
        selection = f"toString({key})" if table == "captures" else key
        rows = self.client.query(
            f"SELECT {selection} AS id, lower(hex(output_digest)) AS digest FROM "
            f"{self.database}.{table} WHERE {key} IN ({','.join(predicates)})",
            parameters={f"id{i}": identity for i, identity in enumerate(identities)},
        )["data"]
        result = {row["id"]: row["digest"] for row in rows}
        if len(rows) != len(result):
            raise ValueError("Duplicate immutable material identity")
        return result

    def content(self, identity: str) -> dict | None:
        rows = self.client.query(
            f"SELECT * REPLACE(lower(hex(content_id)) AS content_id, lower(hex(output_digest)) AS output_digest) "
            f"FROM {self.database}.html_documents AS source WHERE source.document_id={{id:String}} LIMIT 2",
            parameters={"id": identity},
            max_response_bytes=256 * 1024 * 1024,
        )["data"]
        if len(rows) > 1:
            raise ValueError("Duplicate material document")
        if not rows:
            return None
        document = rows[0]
        expected = document.pop("element_count")
        document["elements"] = self.client.query(
            f"SELECT node_index, parent_index, subtree_end_index, sibling_index, depth, "
            f"tag, namespace, attributes, text_direct, text_start, text_end "
            f"FROM {self.database}.html_elements WHERE document_id={{id:String}} ORDER BY node_index",
            parameters={"id": identity}, max_response_bytes=256 * 1024 * 1024,
        )["data"]
        if len(document["elements"]) != expected:
            raise ValueError("Material document element set is incomplete")
        return document

    def complete_many(self, captures: list[Capture]) -> set[UUID]:
        if not captures:
            return set()
        predicates = ','.join(f'{{id{i}:UUID}}' for i in range(len(captures)))
        rows = self.client.query(
            f"SELECT toString(capture_id) AS id, lower(hex(evidence_digest)) AS digest, "
            f"document_id AS document FROM {self.database}.captures "
            f"WHERE capture_id IN ({predicates})",
            parameters={f'id{i}': str(c.capture_id) for i, c in enumerate(captures)},
        )["data"]
        by_id = {r['id']: r for r in rows}
        if len(by_id) != len(rows):
            raise ValueError("Duplicate immutable material identity")
        documents = self.digests('html_documents', list({r['document'] for r in rows if r['document']}))
        completed = set()
        for capture in captures:
            row = by_id.get(str(capture.capture_id))
            if row is None:
                continue
            if row['digest'] != capture.digest:
                raise ValueError("Conflicting material capture identity")
            if row['document'] is None or row['document'] in documents:
                completed.add(capture.capture_id)
        return completed

    @staticmethod
    def _read_source(capture: Capture, archive: Archive) -> bytearray:
        """Read and verify bounded bytes while the caller owns the source claim."""
        payload = capture.payload
        if payload is None:
            raise ValueError("Capture has no payload")
        if payload.byte_length > MAX_INPUT_BYTES:
            raise ValueError(f"HTML {payload.content_id} is {payload.byte_length} bytes; limit is {MAX_INPUT_BYTES} bytes")
        chunks = (
            RawHtmlRepository(archive.store).iter_bytes(payload.object_key)
            if payload.storage_encoding == "zstd"
            else ExactDocumentRepository(archive.store).iter_bytes(payload.object_key)
        )
        source = bytearray()
        for chunk in chunks:
            if len(source) + len(chunk) > payload.byte_length:
                raise ValueError("Raw payload exceeds its declared byte length")
            source.extend(chunk)
        if len(source) != payload.byte_length or sha256(source).hexdigest() != payload.content_id:
            raise ValueError("Raw payload identity mismatch")
        return source

    def project(
        self, capture: Capture, archive: Archive, cache: dict,
        sources: dict | None = None, missing_documents: set[str] | None = None,
    ) -> tuple[dict | None, dict]:
        payload, document, links = capture.payload, None, []
        if payload and payload.media_type.lower() in (
            "text/html",
            "application/xhtml+xml",
        ):
            if payload.byte_length > MAX_INPUT_BYTES:
                raise ValueError(f"HTML {payload.content_id} is {payload.byte_length} bytes; limit is {MAX_INPUT_BYTES} bytes")
            document = cache.get(payload.document_id)
            if document is None and (missing_documents is None or payload.document_id not in missing_documents):
                document = self.content(payload.document_id)
            if document is None:
                source = sources.pop(payload.document_id, None) if sources is not None else None
                if source is None:
                    with write_claims({"content": [payload.content_id]}):
                        source = self._read_source(capture, archive)
                text = source.decode(payload.charset or "utf-8", errors="strict")
                del source
                nodes, elements = parse_document(text)
                del text
                parsed = html_content(payload.content_id, nodes, elements)
                parsed.pop("content_sha256")
                del nodes, elements
                document = output_row(
                    dict(
                        document_id=payload.document_id,
                        content_id=payload.content_id,
                        representation=payload.representation,
                        encoding=payload.charset or "utf-8",
                        **parsed,
                    )
                )
            elements = [
                SimpleNamespace(
                    element_index=e["node_index"],
                    parent_index=e["parent_index"],
                    subtree_end_index=e["subtree_end_index"],
                    tag=e["tag"],
                    attributes=e["attributes"],
                    text_direct=e["text_direct"],
                    text_tail="",
                )
                for e in document["elements"]
            ]
            grouped = links_from_elements(
                elements, page_url=capture.effective_url or capture.requested_url
            )
            links = sorted(
                [
                    dict(
                        node_index=int(link["element_index"]),
                        raw_href=str(link["raw_href"]),
                        target_url=str(link["target_url"]),
                    )
                    for link in (*grouped["internal"], *grouped["external"])
                ],
                key=lambda x: x["node_index"],
            )
            cache[payload.document_id] = document
        elif payload:
            with write_claims({"content": [payload.content_id]}):
                archive.verify_payload(capture)
        return document, capture_row(capture, document, links, archive.location(capture))

    def _insert_verified(self, table: str, rows: dict[str, dict]) -> None:
        if not rows:
            return
        existing = self.digests(table, list(rows))
        if any(
            rows[key]["output_digest"] != digest for key, digest in existing.items()
        ):
            raise ValueError("Conflicting material output")
        missing = [row for key, row in rows.items() if key not in existing]
        if table == "html_documents":
            # The document row is the completion marker. Partial element inserts stay private.
            insert_elements(self.client, self.database, missing)
            insert_json_ld(self.client, self.database, missing)
            missing = [
                {**{k: v for k, v in row.items() if k != "elements"},
                 "element_count": len(row["elements"])} for row in missing
            ]
        self.client.insert_rows(f"{self.database}.{table}", missing)
        if self.digests(table, list(rows)) != {
            key: row["output_digest"] for key, row in rows.items()
        }:
            raise RuntimeError("Material block is incomplete after insert")

    def materialize_many(self, captures: list[Capture], archive: Archive) -> int:
        if len(captures) > 128:
            raise ValueError("Material batch exceeds 128 captures")
        unique = {}
        for capture in captures:
            if (
                capture.capture_id in unique
                and unique[capture.capture_id].digest != capture.digest
            ):
                raise ValueError("Conflicting capture identities in one batch")
            unique[capture.capture_id] = capture
        captures = list(unique.values())
        completed = self.complete_many(captures)
        identities = {c.payload.document_id for c in captures if c.payload and c.capture_id not in completed}
        missing = identities - self.digests("html_documents", list(identities)).keys()
        groups, group, size = [], [], 0
        for capture in captures:
            payload = capture.payload
            amount = payload.byte_length if payload and payload.document_id in missing else 0
            if group and size + amount > BATCH_INPUT_BYTES:
                groups.append(group)
                group, size = [], 0
            group.append(capture)
            size += amount
        if group:
            groups.append(group)
        documents, prepared, size = {}, [], 0

        def flush():
            nonlocal size
            if not prepared:
                return
            with write_claims(
                {
                    "capture": [str(c.capture_id) for c, _ in prepared],
                    "content": [c.payload.content_id for c, _ in prepared if c.payload],
                }
            ):
                retained = [
                    (c, row) for c, row in prepared if not archive.retired(c.capture_id)
                ]
                needed = {
                    row["document_id"] for _, row in retained if row["document_id"]
                }
                self._insert_verified(
                    "html_documents",
                    {key: value for key, value in documents.items() if key in needed},
                )
                self._insert_verified(
                    "captures", {str(c.capture_id): row for c, row in retained}
                )
            missing.difference_update(needed)
            documents.clear()
            prepared.clear()
            size = 0

        for group in groups:
            # Do not retain ordinary output while decoding an oversized source.
            if any(c.payload and c.payload.byte_length > BATCH_INPUT_BYTES for c in group):
                flush()
            for capture, document, row in self._project_group(group, archive, completed, missing, documents):
                amount = row_bytes(row) + (row_bytes(document) if document else 0)
                if prepared and size + amount > INSERT_TARGET_BYTES:
                    flush()
                if document is not None:
                    documents[capture.payload.document_id] = document
                prepared.append((capture, row))
                size += amount
                if size >= INSERT_TARGET_BYTES:
                    flush()
                del document, row
        flush()
        completed = self.complete_many(captures)
        for capture in captures:
            if not archive.retired(capture.capture_id) and capture.capture_id not in completed:
                raise RuntimeError("Capture verification failed")
        return len(captures)

    def _project_group(self, captures, archive, completed, missing, documents):
        # Source ownership is bounded independently of the pending output block.
        needs = [c for c in captures if c.payload and c.payload.document_id in missing
                 and c.payload.document_id not in documents
                 and c.capture_id not in completed
                 and c.payload.media_type.lower() in ("text/html", "application/xhtml+xml")]
        sources = {}
        if needs:
            with write_claims({"content": [c.payload.content_id for c in needs]}):
                for capture in needs:
                    try:
                        if not archive.retired(capture.capture_id) and capture.payload.document_id not in sources:
                            sources[capture.payload.document_id] = self._read_source(capture, archive)
                    except Exception as exc:
                        raise MaterialInputError(capture.capture_id, exc) from exc
        for capture in captures:
            try:
                if archive.retired(capture.capture_id):
                    self.retire(capture, archive)
                    continue
                if capture.capture_id in completed:
                    continue
                document, row = self.project(capture, archive, documents, sources, missing)
                yield capture, document, row
                del document, row
            except Exception as exc:
                raise MaterialInputError(capture.capture_id, exc) from exc

    def retire(self, capture: Capture, archive: Archive) -> None:
        if not archive.retired(capture.capture_id):
            raise ValueError("Capture retirement is not archived")
        with write_claims(
            {
                "capture": [str(capture.capture_id)],
                "content": [capture.payload.content_id] if capture.payload else [],
            }
        ):
            self.client.execute(
                f"ALTER TABLE {self.database}.captures DELETE WHERE capture_id={{id:UUID}} SETTINGS mutations_sync=2",
                parameters={"id": str(capture.capture_id)},
            )
            if capture.payload:
                document = capture.payload.document_id
                count = self.client.query(
                    f"SELECT count() AS n FROM {self.database}.captures WHERE document_id={{id:String}}",
                    parameters={"id": document},
                )["data"][0]["n"]
                if count == 0:
                    # Remove the visibility marker before reclaiming even a partial element set.
                    for table in ("html_documents", "html_elements", "json_ld"):
                        self.client.execute(
                            f"ALTER TABLE {self.database}.{table} DELETE WHERE document_id={{id:String}} SETTINGS mutations_sync=2",
                            parameters={"id": document},
                        )
