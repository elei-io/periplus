"""Deterministic material output from archived captures; no operational evidence."""

from hashlib import sha256
from importlib.resources import files
import re
from types import SimpleNamespace
from uuid import UUID

from periplus.ingestion.archive import Archive, capture_key
from periplus.ingestion.captures import Capture, canonical
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.ingestion.objects.document import ExactDocumentRepository
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.dom.links import links_from_elements
from periplus.materialization.html_content import html_content
from periplus.platform.clickhouse import ClickHouseClient
from periplus.retention.identities import write_claims

MAX_OUTPUT_BYTES = 31 * 1024 * 1024


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


def output_row(value: dict) -> dict:
    encoded = canonical(value)
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("Material output exceeds row byte budget")
    return {**value, "output_digest": sha256(encoded).hexdigest()}


def capture_row(capture: Capture, document: dict | None, links: list) -> dict:
    payload, source = capture.payload, capture.source
    return output_row(
        dict(
            capture_id=str(capture.capture_id),
            evidence_digest=capture.digest,
            requested_url=capture.requested_url,
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
            archive_record_key=capture_key(capture.capture_id),
            links=links,
        )
    )


class MaterialStore:
    def __init__(self, client: ClickHouseClient, database: str = "material"):
        self.client, self.database = client, material_database(database)

    def validate(self) -> None:
        for table in ("captures", "html_documents"):
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
            f"{{id{i}:UUID}}" if table == "captures" else f"unhex({{id{i}:String}})"
            for i in range(len(identities))
        ]
        selection = f"toString({key})" if table == "captures" else f"lower(hex({key}))"
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
            f"SELECT * REPLACE(lower(hex(document_id)) AS document_id, "
            f"lower(hex(content_id)) AS content_id, lower(hex(output_digest)) AS output_digest) "
            f"FROM {self.database}.html_documents WHERE document_id=unhex({{id:String}}) LIMIT 2",
            parameters={"id": identity},
            max_response_bytes=48 * 1024 * 1024,
        )["data"]
        if len(rows) > 1:
            raise ValueError("Duplicate material document")
        return rows[0] if rows else None

    def complete(self, capture: Capture) -> bool:
        rows = self.client.query(
            f"SELECT lower(hex(evidence_digest)) AS digest, "
            f"lower(hex(document_id)) AS document FROM {self.database}.captures WHERE capture_id={{id:UUID}} LIMIT 2",
            parameters={"id": str(capture.capture_id)},
        )["data"]
        if not rows:
            return False
        if len(rows) != 1 or rows[0]["digest"] != capture.digest:
            raise ValueError("Conflicting material capture identity")
        return rows[0]["document"] is None or bool(
            self.digests("html_documents", [rows[0]["document"]])
        )

    def project(
        self, capture: Capture, archive: Archive, cache: dict
    ) -> tuple[dict | None, dict]:
        payload, document, links = capture.payload, None, []
        if payload and payload.media_type.lower() in (
            "text/html",
            "application/xhtml+xml",
        ):
            if payload.byte_length > 32 * 1024 * 1024:
                raise ValueError("HTML exceeds material input budget")
            document = cache.get(payload.document_id) or self.content(
                payload.document_id
            )
            if document is None:
                # Source protection covers only the bounded byte read. Parsing does
                # not hold a distributed claim; final publication rechecks retirement.
                with write_claims({"content": [payload.content_id]}):
                    chunks = (
                        RawHtmlRepository(archive.store).iter_bytes(payload.object_key)
                        if payload.storage_encoding == "zstd"
                        else ExactDocumentRepository(archive.store).iter_bytes(
                            payload.object_key
                        )
                    )
                    source = bytearray()
                    for chunk in chunks:
                        if len(source) + len(chunk) > payload.byte_length:
                            raise ValueError(
                                "Raw payload exceeds its declared byte length"
                            )
                        source.extend(chunk)
                    if (
                        len(source) != payload.byte_length
                        or sha256(source).hexdigest() != payload.content_id
                    ):
                        raise ValueError("Raw payload identity mismatch")
                text = source.decode(payload.charset or "utf-8", errors="strict")
                nodes, elements = parse_document(text)
                parsed = html_content(payload.content_id, nodes, elements).model_dump(
                    mode="json"
                )
                parsed.pop("content_sha256")
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
        return document, capture_row(capture, document, links)

    def _insert_verified(self, table: str, rows: dict[str, dict]) -> None:
        if not rows:
            return
        existing = self.digests(table, list(rows))
        if any(
            rows[key]["output_digest"] != digest for key, digest in existing.items()
        ):
            raise ValueError("Conflicting material output")
        block, size = [], 0
        for key, row in rows.items():
            if key in existing:
                continue
            amount = len(canonical(row))
            if block and size + amount > 8 * 1024 * 1024:
                self.client.insert_rows(f"{self.database}.{table}", block)
                block, size = [], 0
            block.append(row)
            size += amount
        self.client.insert_rows(f"{self.database}.{table}", block)
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
            documents.clear()
            prepared.clear()
            size = 0

        for capture in captures:
            try:
                if archive.retired(capture.capture_id):
                    self.retire(capture, archive)
                    continue
                if self.complete(capture):
                    continue
                document, row = self.project(capture, archive, documents)
                prepared.append((capture, row))
                size += len(canonical(row)) + (
                    len(canonical(document)) if document else 0
                )
                if size >= 8 * 1024 * 1024:
                    flush()
            except Exception as exc:
                raise MaterialInputError(capture.capture_id, exc) from exc
        flush()
        for capture in captures:
            if not archive.retired(capture.capture_id) and not self.complete(capture):
                raise RuntimeError("Capture verification failed")
        return len(captures)

    def retire(self, capture: Capture, archive: Archive) -> None:
        if not archive.retired(capture.capture_id):
            raise ValueError("Capture retirement is not archived")
        if not self.digests("captures", [str(capture.capture_id)]):
            return
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
                    f"SELECT count() AS n FROM {self.database}.captures WHERE document_id=unhex({{id:String}})",
                    parameters={"id": document},
                )["data"][0]["n"]
                if count == 0:
                    self.client.execute(
                        f"ALTER TABLE {self.database}.html_documents DELETE WHERE document_id=unhex({{id:String}}) SETTINGS mutations_sync=2",
                        parameters={"id": document},
                    )
