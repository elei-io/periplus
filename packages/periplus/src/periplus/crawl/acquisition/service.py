"""Acquire one URL and freeze its immutable observation evidence for runtime acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from playwright.async_api import Browser

from periplus.crawl.acquisition.capture import capture_page
from periplus.crawl.acquisition.evidence import attempt_records, step_records
from periplus.crawl.acquisition.errors import RetryableAcquisitionFailure
from periplus.crawl.acquisition.models import AcquisitionResult
from periplus.urls import normalize_url
from periplus.platform.catalogue.records import AttemptUsage
from periplus.platform.catalogue import (
    DocumentRecord,
    VisitEvidence,
    VisitRecord,
    document_id_for,
)
from periplus.ingestion.acquisition import AcquisitionPipeline
from periplus.ingestion.objects.document import ExactDocumentIdentity, detect_media_type
from periplus.ingestion.objects.html import identify_html
from periplus.crawl.acquisition.context import AcquisitionContext
from periplus.crawl.runtime.domain_pacing import (
    record_domain_response,
)

logger = logging.getLogger(__name__)


async def acquire_page(
    *,
    url: str,
    context: AcquisitionContext,
    browser: Browser,
    repository_pipeline: AcquisitionPipeline | None = None,
    domain_pacing=None,
    domain_permit=None,
    domain_start_reserved: bool = False,
    persist_retryable_failure: bool = True,
    include_html: bool = False,
) -> AcquisitionResult:
    if (domain_start_reserved and domain_permit is None) or (domain_pacing is not None and not domain_start_reserved):
        raise ValueError("a reserved domain start requires a held permit")
    effective = context.policy
    policy = effective.content
    domain = effective.domain
    normalized = normalize_url(url)
    remote_domain = (urlparse(normalized).hostname or "unknown").lower()
    attempt_number = len(context.prior_attempts) + 1

    async def perform_capture():
        if context.attempt_reserved_ms < 6000:
            raise ValueError("capture requires a frozen physical allowance")
        started = time.perf_counter()
        result = await capture_page(
            normalized, policy, attempt_number=attempt_number, browser=browser,
            timeout_seconds=(context.attempt_reserved_ms - 5000) / 1000,
            exclusions=context.exclusions,
        )
        from periplus.operations.metrics import capture_outcomes, capture_duration, capture_throttled
        capture_duration.observe(time.perf_counter() - started)
        status_class = f"{result.status_code // 100}xx" if result.status_code and 100 <= result.status_code < 600 else "unknown"
        capture_outcomes.labels("succeeded" if result.success else "failed", status_class).inc()
        if result.status_code == 429:
            capture_throttled.inc()
        if result.attempt_evidence is None:
            raise ValueError("capture returned no physical attempt evidence")
        usage = AttemptUsage(
            policy_version=context.dispatch_policy_version,
            domain_policy=context.policy.domain, exclusion_policy_version=context.exclusion_policy_version,
            reserved_ms=context.attempt_reserved_ms,
            measured_ms=math.ceil((time.perf_counter() - started) * 1000),
        )
        return result.model_copy(update={"attempt_evidence": result.attempt_evidence.model_copy(
            update={"resource_usage": usage},
        )})

    if domain_permit is not None:
        async with domain_permit:
            page = await perform_capture()
    else:
        page = await perform_capture()
    if domain_pacing is not None:
        try:
            await record_domain_response(
                domain_pacing,
                domain=remote_domain,
                status_code=page.status_code,
                retry_after_seconds=page.retry_after_seconds,
            )
        except Exception:
            logger.warning(
                "failed to record adaptive domain pacing for %s",
                remote_domain,
                exc_info=True,
            )
    if (
        not page.success
        and page.failure_retryable
        and not persist_retryable_failure
    ):
        raise RetryableAcquisitionFailure(page)
    html_identity = (
        identify_html(page.html) if page.html is not None and page.success else None
    )
    exact_identity = (
        ExactDocumentIdentity(
            sha256=hashlib.sha256(page.document_bytes).hexdigest(),
            size_bytes=len(page.document_bytes),
        )
        if page.document_bytes is not None and page.success
        else None
    )
    attempt_evidence = context.prior_attempts + ((page.attempt_evidence,) if page.attempt_evidence is not None else ())
    attempts = attempt_records(context.acquisition_id, attempt_evidence)
    if not attempts:
        raise RuntimeError("visit acquisition produced no attempt evidence")
    finished_at = datetime.now(UTC)
    observed_at = (
        finished_at
        if html_identity is not None or exact_identity is not None
        else None
    )
    final_normalized = (
        attempts[-1].effective_url
        if attempts[-1].effective_url is not None
        else None
    )
    steps = step_records(context.acquisition_id, (*context.prior_steps, *page.steps))

    async def persist(pipeline: AcquisitionPipeline) -> AcquisitionResult:
        source_url = final_normalized or normalized
        document: DocumentRecord | None = None
        document_id = (
            document_id_for(context.acquisition_id)
            if observed_at is not None
            else None
        )
        if html_identity is not None:
            assert observed_at is not None and document_id is not None
            stored = await pipeline.store_html(
                captured_html=page.html or "",
                source_url=source_url,
                visit_id=context.acquisition_id,
                observed_at=observed_at,
                content_type=page.response_media_type or "text/html",
                identity=html_identity,
            )
            document = DocumentRecord(
                document_id=document_id,
                visit_id=context.acquisition_id,
                attempt_id=attempts[-1].attempt_id,
                observed_at=observed_at,
                representation="rendered_html",
                declared_media_type=page.response_media_type,
                detected_media_type="text/html",
                charset="utf-8",
                content_sha256=stored.sha256,
                content_bytes=stored.size_bytes,
                object_key=stored.object_key,
                storage_encoding=stored.compression,
                stored_bytes=stored.compressed_size_bytes,
            )
        if exact_identity is not None:
            assert observed_at is not None and document_id is not None
            content = page.document_bytes or b""
            detection = await asyncio.to_thread(detect_media_type, content)
            stored = await pipeline.store_document(
                content=io.BytesIO(content),
                identity=exact_identity,
                source_url=source_url,
                visit_id=context.acquisition_id,
                observed_at=observed_at,
                content_type=page.response_media_type or "application/octet-stream",
            )
            document = DocumentRecord(
                document_id=document_id,
                visit_id=context.acquisition_id,
                attempt_id=attempts[-1].attempt_id,
                observed_at=observed_at,
                representation="response_body",
                declared_media_type=page.response_media_type,
                detected_media_type=detection.media_type,
                charset=None,
                content_sha256=stored.sha256,
                content_bytes=stored.size_bytes,
                object_key=stored.object_key,
                storage_encoding="identity",
                stored_bytes=stored.size_bytes,
            )
        visit = VisitRecord(
            visit_id=context.acquisition_id,
            requested_url=normalized,
            effective_url=final_normalized,
            admitted_at=context.admitted_at,
            started_at=attempts[0].started_at,
            observed_at=observed_at,
            finished_at=finished_at,
            outcome={
                "success": "succeeded",
                "failed": "failed",
                "skipped": "skipped",
            }[page.outcome],
            status_code=page.status_code,
            document_id=document_id,
        )
        evidence = VisitEvidence(
            visit=visit,
            attempts=attempts,
            steps=steps,
            document=document,
        )
        return page.model_copy(
            update={
                "visit_id": context.acquisition_id,
                "evidence": evidence,
                "document_id": document_id,
                "content_sha256": (
                    document.content_sha256 if document is not None else None
                ),
                "html": page.html if include_html else None,
                "document_bytes": None,
            }
        )

    if repository_pipeline is not None:
        return await persist(repository_pipeline)
    async with AcquisitionPipeline() as pipeline:
        return await persist(pipeline)
