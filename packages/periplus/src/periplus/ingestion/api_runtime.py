"""Process-owned external evidence ingestion service."""

from fastapi import Request

from periplus.ingestion.external import EvidenceImportService


def get_evidence_import_service(request: Request) -> EvidenceImportService:
    service = getattr(request.app.state, "evidence_import_service", None)
    if not isinstance(service, EvidenceImportService):
        raise RuntimeError("evidence import service is unavailable")
    return service
