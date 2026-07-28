"""Acquire one graph request and publish its immutable visit evidence."""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

from playwright.async_api import Playwright

from atlas.crawl.acquisition.capture import capture_page
from atlas.crawl.acquisition.errors import RetryableAcquisitionFailure
from atlas.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
from atlas.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from atlas.urls import normalize_url
from atlas.platform.catalogue import (
    AttemptRecord,
    DocumentRecord,
    StepRecord,
    VisitEvidence,
    VisitRecord,
    attempt_id_for,
    document_id_for,
)
from atlas.ingestion.acquisition import AcquisitionPipeline
from atlas.ingestion.objects.document import ExactDocumentIdentity, detect_media_type
from atlas.ingestion.objects.html import identify_html
from atlas.crawl.runtime.context import GraphExecutionContext, graph_execution_scope
from atlas.crawl.runtime.domain_pacing import (
    domain_permit as acquire_domain_permit,
)
from atlas.crawl.runtime.domain_pacing import (
    record_domain_response,
    wait_for_domain_interval,
)

logger = logging.getLogger(__name__)


async def acquire_page(
    *,
    url: str,
    context: GraphExecutionContext,
    playwright: Playwright,
    repository_pipeline: AcquisitionPipeline | None = None,
    domain_pacing=None,
    domain_permit=None,
    persist_retryable_failure: bool = True,
    include_html: bool = False,
) -> AcquisitionResult:
    effective = EffectivePolicySnapshot.model_validate(
        context.effective_policy_snapshot_json
    )
    policy = effective.content
    domain = effective.domain
    normalized = normalize_url(url)
    remote_domain = (urlparse(normalized).hostname or "unknown").lower()
    attempt_number = len(context.prior_attempts_json) + 1
    with graph_execution_scope(context):
        if domain_permit is not None:
            async with domain_permit:
                if domain_pacing is not None:
                    await wait_for_domain_interval(
                        domain_pacing,
                        domain=remote_domain,
                        interval_seconds=domain.minimum_request_interval_seconds,
                    )
                page = await capture_page(
                    normalized,
                    policy,
                    attempt_number=attempt_number,
                    playwright=playwright,
                )
        elif domain_pacing is not None:
            async with acquire_domain_permit(
                domain_pacing,
                domain=remote_domain,
                concurrency=domain.maximum_concurrency,
            ):
                if domain_pacing is not None:
                    await wait_for_domain_interval(
                        domain_pacing,
                        domain=remote_domain,
                        interval_seconds=domain.minimum_request_interval_seconds,
                    )
                page = await capture_page(
                    normalized,
                    policy,
                    attempt_number=attempt_number,
                    playwright=playwright,
                )
        else:
            page = await capture_page(
                normalized,
                policy,
                attempt_number=attempt_number,
                playwright=playwright,
            )
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
        attempt_evidence = tuple(
            AcquisitionAttemptEvidence.model_validate(value)
            for value in context.prior_attempts_json
        ) + ((page.attempt_evidence,) if page.attempt_evidence is not None else ())
        attempts = tuple(
            AttemptRecord(
                attempt_id=attempt_id_for(context.crawl_request_id, index),
                visit_id=context.crawl_request_id,
                attempt_index=index,
                started_at=attempt.started_at,
                finished_at=attempt.completed_at,
                effective_url=(
                    normalize_url(attempt.final_url)
                    if attempt.final_url is not None
                    else None
                ),
                status_code=attempt.status_code,
                outcome=(
                    "succeeded"
                    if attempt.outcome in {"success", "skipped"}
                    else "failed"
                ),
                failure_stage=(
                    attempt.failure_stage
                    if attempt.outcome in {"retry", "failed"}
                    else None
                ),
                failure_code=(
                    attempt.failure_code or "acquisition_failed"
                    if attempt.outcome in {"retry", "failed"}
                    else None
                ),
                failure_message=(
                    attempt.failure_message or attempt.failure_code
                    if attempt.outcome in {"retry", "failed"}
                    else None
                ),
            )
            for index, attempt in enumerate(attempt_evidence)
        )
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
        steps = tuple(
            StepRecord(
                attempt_id=attempt_id_for(
                    context.crawl_request_id,
                    step.attempt_number - 1,
                ),
                step_index=step.step_ordinal - 1,
                action=step.method,
                parameters={
                    "action_version": step.method_version,
                    "config": step.config_json,
                },
                started_at=step.started_at,
                duration_ms=step.duration_ms,
                outcome="succeeded",
                stopping_reason=step.stop_reason,
            )
            for step in page.steps
        )

        async def persist(pipeline: AcquisitionPipeline) -> AcquisitionResult:
            source_url = final_normalized or normalized
            document: DocumentRecord | None = None
            document_id = (
                document_id_for(context.crawl_request_id)
                if observed_at is not None
                else None
            )
            if html_identity is not None:
                assert observed_at is not None and document_id is not None
                stored = await pipeline.store_html(
                    captured_html=page.html or "",
                    source_url=source_url,
                    visit_id=context.crawl_request_id,
                    observed_at=observed_at,
                    content_type=page.response_media_type or "text/html",
                    identity=html_identity,
                )
                document = DocumentRecord(
                    document_id=document_id,
                    visit_id=context.crawl_request_id,
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
                    visit_id=context.crawl_request_id,
                    observed_at=observed_at,
                    content_type=page.response_media_type or "application/octet-stream",
                )
                document = DocumentRecord(
                    document_id=document_id,
                    visit_id=context.crawl_request_id,
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
                visit_id=context.crawl_request_id,
                crawl_id=context.graph_run_id,
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
            await pipeline.enqueue_visit(evidence)
            return page.model_copy(
                update={
                    "visit_id": context.crawl_request_id,
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
