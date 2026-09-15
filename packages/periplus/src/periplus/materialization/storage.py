"""Append-only HTML/link publication with a final visit-scoped readiness row."""
from hashlib import sha256
from importlib.resources import files
from uuid import UUID
import re
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from periplus.ingestion.queue import visit_ingestion_job
from periplus.ingestion.service import RepositoryIngestor
from periplus.ingestion.storage import evidence_digest
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.dom.links import links_from_elements
from periplus.materialization.html_content import HtmlContent, html_content
from periplus.platform.catalogue.exceptions import CatalogueConflictError
from periplus.platform.catalogue.records import VisitEvidence, canonical_json, link_id_for, link_occurrence_id_for
from periplus.platform.clickhouse import ClickHouseClient
from periplus.retention.identities import write_claims

_MAX_HTML_BYTES = 32 * 1024 * 1024
_SCOPES = {"same_url": "self", "same_path": "same_origin", "same_origin": "same_origin",
           "same_host": "same_host", "same_site": "same_site", "external": "external"}


class MaterialInputError(ValueError):
    def __init__(self, identity: UUID, cause: Exception):
        super().__init__(f"visit {identity}: {type(cause).__name__}")


class MaterialLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    occurrence_id: UUID
    link_id: UUID
    element_index: int = Field(ge=0)
    raw_href: str
    source_url: str
    target_url: str
    relation_scope: str


class VisitMaterial(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    visit_id: UUID
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    html_content_sha256: str | None
    links: tuple[MaterialLink, ...]


def material_database(value: str) -> str:
    if not re.fullmatch(r"material(?:_[0-9a-f]{32})?", value):
        raise ValueError("Invalid material target")
    return value


def install_material_schema(client: ClickHouseClient, database: str = "material") -> None:
    source = files("periplus.materialization").joinpath("schema.sql").read_text()
    source = re.sub(r"\bmaterial\b", material_database(database), source)
    for statement in source.split(";"):
        if statement.strip():
            client.execute(statement)


def _row(value: BaseModel) -> dict[str, JsonValue]:
    payload = value.model_dump(mode="json")
    encoded = canonical_json(payload).encode()
    if len(encoded) > 31 * 1024 * 1024:
        raise ValueError("material output exceeds the single-row publication budget")
    payload["output_sha256"] = sha256(encoded).hexdigest()
    return payload


def build_material(evidence: VisitEvidence, ingestor: RepositoryIngestor, existing: HtmlContent | None = None) -> tuple[HtmlContent | None, VisitMaterial]:
    document = evidence.document
    is_html = document is not None and (document.representation == "rendered_html"
        or document.detected_media_type in {"text/html", "application/xhtml+xml"})
    if is_html and document.content_bytes > _MAX_HTML_BYTES:
        raise ValueError("HTML exceeds the materializer input budget")
    # Only missing HTML output needs raw I/O. Reused content is immutable and
    # visit publication still acquires exact source-retirement/write claims.
    if is_html and existing is None:
        ingestor.prepare(visit_ingestion_job(evidence))
    content = None
    links = []
    if is_html:
        if existing is None:
            if document.storage_encoding == "zstd":
                source = ingestor.html_repository.read(document.object_key)
            else:
                source = ingestor.document_repository.read_bytes(document.object_key)
            nodes, elements = parse_document(source)
            content = html_content(document.content_sha256, nodes, elements)
        else:
            if existing.content_sha256 != document.content_sha256:
                raise ValueError("Content reuse identity mismatch")
            content = existing
            elements = [SimpleNamespace(element_index=e.node_index, parent_index=e.parent_index,
                subtree_end_index=e.subtree_end_index, tag=e.tag, attributes=e.attributes,
                text_direct=e.text_direct, text_tail="") for e in existing.elements]
        grouped = links_from_elements(elements, page_url=evidence.visit.effective_url or evidence.visit.requested_url)
        for link in (*grouped["internal"], *grouped["external"]):
            index = int(link["element_index"])
            source_url, target_url = str(link["source_url"]), str(link["target_url"])
            links.append(MaterialLink(occurrence_id=link_occurrence_id_for(document.document_id, index),
                link_id=link_id_for(source_url, target_url), element_index=index,
                raw_href=str(link["raw_href"]), source_url=source_url, target_url=target_url,
                relation_scope=_SCOPES[str(link["relation_kind"])]))
    return content, VisitMaterial(visit_id=evidence.visit.visit_id, evidence_sha256=evidence_digest(evidence),
        html_content_sha256=content.content_sha256 if content else None,
        links=tuple(sorted(links, key=lambda item: item.element_index)))


class MaterialStore:
    def __init__(self, client: ClickHouseClient, database: str = "material") -> None:
        self.client = client
        self.database = material_database(database)

    def validate(self) -> None:
        for table in ("html_documents", "visit_results"):
            self.client.execute(f"SELECT output_sha256 FROM {self.database}.{table} LIMIT 0")

    def _matches(self, table: str, key: str, identity: str, digest: str) -> bool:
        if (table, key) not in {("html_documents", "content_sha256"), ("visit_results", "visit_id")}:
            raise ValueError("unknown material identity")
        predicate = f"{key}=unhex({{identity:String}})" if key == "content_sha256" else f"{key}={{identity:UUID}}"
        rows = self.client.query(f"SELECT lower(hex(output_sha256)) AS digest FROM {self.database}.{table} "
            f"WHERE {predicate} LIMIT 2", parameters={"identity": identity})["data"]
        if not rows:
            return False
        if len(rows) != 1 or rows[0]["digest"] != digest:
            raise CatalogueConflictError("material identity has different or duplicate output")
        return True

    def publish(self, content: HtmlContent | None, visit: VisitMaterial) -> bool:
        # Validate both block budgets before the first write. The final visit row
        # contains all link output and is appended only after content is durable.
        if visit.html_content_sha256 != (content.content_sha256 if content else None):
            raise ValueError("visit readiness must reference its complete HTML output")
        content_row = _row(content) if content else None
        visit_row = _row(visit)
        with write_claims({"observation": [str(visit.visit_id)],
                           "content": [content.content_sha256] if content else []}):
            if content_row and not self._matches("html_documents", "content_sha256",
                                                  content.content_sha256, content_row["output_sha256"]):
                self.client.insert_json(f"{self.database}.html_documents", content_row)
                if not self._matches("html_documents", "content_sha256", content.content_sha256, content_row["output_sha256"]):
                    raise RuntimeError("acknowledged HTML output is not visible on its write route")
            if self._matches("visit_results", "visit_id", str(visit.visit_id), visit_row["output_sha256"]):
                return False
            self.client.insert_json(f"{self.database}.visit_results", visit_row)
            if not self._matches("visit_results", "visit_id", str(visit.visit_id), visit_row["output_sha256"]):
                raise RuntimeError("acknowledged visit output is not visible on its write route")
            return True

    def content(self, digest: str) -> HtmlContent | None:
        rows = self.client.query(f"SELECT lower(hex(content_sha256)) AS content_sha256, document_text, elements "
            f"FROM {self.database}.html_documents AS d WHERE d.content_sha256=unhex({{digest:String}}) LIMIT 2",
            parameters={"digest": digest})["data"]
        if len(rows) > 1:
            raise CatalogueConflictError("Duplicate content output")
        return HtmlContent.model_validate(rows[0]) if rows else None

    def complete(self, evidence: VisitEvidence) -> bool:
        rows = self.client.query(f"SELECT lower(hex(evidence_sha256)) AS digest, lower(hex(html_content_sha256)) AS content FROM {self.database}.visit_results "
            "WHERE visit_id={id:UUID} LIMIT 2", parameters={"id": str(evidence.visit.visit_id)})["data"]
        if not rows:
            return False
        if len(rows) != 1 or rows[0]["digest"] != evidence_digest(evidence):
            raise CatalogueConflictError("Conflicting materialized visit evidence")
        if rows[0]["content"] is not None:
            count = self.client.query(f"SELECT count() AS n FROM {self.database}.html_documents "
                "WHERE content_sha256=unhex({digest:String})", parameters={"digest": rows[0]["content"]})["data"][0]["n"]
            if count > 1:
                raise CatalogueConflictError("Duplicate materialized content")
            return count == 1
        return True

    def materialize(self, evidence: VisitEvidence, ingestor: RepositoryIngestor) -> bool:
        if self.complete(evidence):
            return False
        existing = self.content(evidence.document.content_sha256) if evidence.document else None
        content, visit = build_material(evidence, ingestor, existing)
        return self.publish(content, visit)

    def digests(self, table: str, key: str, identities: list[str]) -> dict[str, str]:
        if not identities:
            return {}
        if (table, key) not in {("html_documents", "content_sha256"), ("visit_results", "visit_id")}:
            raise ValueError("Unknown material identity")
        parameters = {f"id{i}": value for i, value in enumerate(identities)}
        expressions = [f"unhex({{id{i}:String}})" if key == "content_sha256" else f"{{id{i}:UUID}}" for i in range(len(identities))]
        identity_sql = f"lower(hex({key}))" if key == "content_sha256" else f"toString({key})"
        rows = self.client.query(f"SELECT {identity_sql} AS identity, lower(hex(output_sha256)) AS digest "
            f"FROM {self.database}.{table} WHERE {key} IN ({','.join(expressions)})", parameters=parameters)["data"]
        result = {row["identity"]: row["digest"] for row in rows}
        if len(result) != len(rows):
            raise CatalogueConflictError("Duplicate material output")
        return result

    def publish_batch(self, contents: dict[str, HtmlContent], visits: list[VisitMaterial]) -> None:
        content_rows = {key: _row(value) for key, value in contents.items()}
        visit_rows = {str(value.visit_id): _row(value) for value in visits}
        if len(visit_rows) != len(visits):
            raise ValueError("Duplicate visits within a publication block")
        with write_claims({"observation": list(visit_rows), "content": list(content_rows)}):
            for table, key, rows in (("html_documents", "content_sha256", content_rows), ("visit_results", "visit_id", visit_rows)):
                existing = self.digests(table, key, list(rows))
                if any(rows[identity]["output_sha256"] != digest for identity, digest in existing.items()):
                    raise CatalogueConflictError("Conflicting material output")
                missing = [value for identity, value in rows.items() if identity not in existing]
                # Bound each INSERT by serialized bytes; a single large legal row
                # remains one block. Verification precedes every durable checkpoint.
                block, size = [], 0
                for value in missing:
                    amount = len(canonical_json(value).encode())
                    if block and size + amount > 8 * 1024 * 1024:
                        self.client.insert_rows(f"{self.database}.{table}", block)
                        block, size = [], 0
                    block.append(value)
                    size += amount
                self.client.insert_rows(f"{self.database}.{table}", block)
                if self.digests(table, key, list(rows)) != {identity: value["output_sha256"] for identity, value in rows.items()}:
                    raise RuntimeError("Material block is not fully visible after acknowledged insert")

    def materialize_many(self, evidence: list[VisitEvidence], ingestor: RepositoryIngestor) -> None:
        if len(evidence) > 128:
            raise ValueError("Material page exceeds 128 visits")
        completed = self.digests("visit_results", "visit_id", [str(item.visit.visit_id) for item in evidence])
        contents, visits, size = {}, [], 0
        for item in evidence:
            if str(item.visit.visit_id) in completed:
                if not self.complete(item):
                    raise RuntimeError("Material output changed during reconciliation")
                continue
            digest = item.document.content_sha256 if item.document else None
            existing = contents.get(digest) or (self.content(digest) if digest else None)
            try:
                content, visit = build_material(item, ingestor, existing)
            except Exception as exc:
                raise MaterialInputError(item.visit.visit_id, exc) from exc
            amount = len(canonical_json(visit.model_dump(mode="json")).encode())
            if content and content.content_sha256 not in contents:
                amount += len(canonical_json(content.model_dump(mode="json")).encode())
            if visits and size + amount > 8 * 1024 * 1024:
                self.publish_batch(contents, visits)
                contents, visits, size = {}, [], 0
            if content:
                contents[content.content_sha256] = content
            visits.append(visit)
            size += amount
        if visits:
            self.publish_batch(contents, visits)
