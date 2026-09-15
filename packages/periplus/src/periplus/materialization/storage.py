"""Append-only HTML/link publication with a final visit-scoped readiness row."""
from hashlib import sha256
from importlib.resources import files
from uuid import UUID

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


def install_material_schema(client: ClickHouseClient) -> None:
    source = files("periplus.materialization").joinpath("schema.sql").read_text()
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


def build_material(evidence: VisitEvidence, ingestor: RepositoryIngestor) -> tuple[HtmlContent | None, VisitMaterial]:
    document = evidence.document
    is_html = document is not None and (document.representation == "rendered_html"
        or document.detected_media_type in {"text/html", "application/xhtml+xml"})
    if is_html and document.content_bytes > _MAX_HTML_BYTES:
        raise ValueError("HTML exceeds the materializer input budget")
    # This verifies retained bytes and establishes the same publication protection
    # used by base ingestion, independently of whether base ingestion has finished.
    ingestor.prepare(visit_ingestion_job(evidence))
    content = None
    links = []
    if is_html:
        if document.storage_encoding == "zstd":
            source = ingestor.html_repository.read(document.object_key)
        else:
            source = ingestor.document_repository.read_bytes(document.object_key)
        nodes, elements = parse_document(source)
        content = html_content(document.content_sha256, nodes, elements)
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
    def __init__(self, client: ClickHouseClient) -> None:
        self.client = client

    def validate(self) -> None:
        for table in ("html_documents", "visit_results"):
            self.client.execute(f"SELECT output_sha256 FROM material.{table} LIMIT 0")

    def _matches(self, table: str, key: str, identity: str, digest: str) -> bool:
        if (table, key) not in {("html_documents", "content_sha256"), ("visit_results", "visit_id")}:
            raise ValueError("unknown material identity")
        predicate = f"{key}=unhex({{identity:String}})" if key == "content_sha256" else f"{key}={{identity:UUID}}"
        rows = self.client.query(f"SELECT lower(hex(output_sha256)) AS digest FROM material.{table} "
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
                self.client.insert_json("material.html_documents", content_row)
                if not self._matches("html_documents", "content_sha256", content.content_sha256, content_row["output_sha256"]):
                    raise RuntimeError("acknowledged HTML output is not visible on its write route")
            if self._matches("visit_results", "visit_id", str(visit.visit_id), visit_row["output_sha256"]):
                return False
            self.client.insert_json("material.visit_results", visit_row)
            if not self._matches("visit_results", "visit_id", str(visit.visit_id), visit_row["output_sha256"]):
                raise RuntimeError("acknowledged visit output is not visible on its write route")
            return True
