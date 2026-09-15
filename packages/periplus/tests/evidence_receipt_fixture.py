from datetime import UTC, datetime
from periplus.ingestion.storage import EvidenceReceipt, evidence_digest


def receipt_for(job, *, created=True, ingested_at=None):
    evidence = job.visit or job.lineage
    return EvidenceReceipt(kind="visit" if job.visit else job.lineage.kind,
        identity=job.identity, evidence_sha256=evidence_digest(evidence),
        ingested_at=ingested_at or datetime.now(UTC), created=created)
