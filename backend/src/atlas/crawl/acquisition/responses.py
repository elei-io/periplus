"""Classify acquisition responses and construct terminal outcomes."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from atlas.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionResult
from atlas.crawl.control.crawl_policies.schemas import ResponseOutcome


def status_outcome(status: int | None, policy) -> ResponseOutcome:
    if status is None:
        return "accept"
    for rule in policy.content.response_rules.http_status:
        if rule.matches(status):
            return rule.outcome
    return "accept"


def parse_media_type(value: str | None) -> str:
    return (value or "text/html").partition(";")[0].strip().lower()


def accepted_media_type(media_type: str, accepted: tuple[str, ...]) -> bool:
    return any(
        value == media_type
        or (value.endswith("/*") and media_type.startswith(value[:-1]))
        for value in accepted
    )


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isdigit():
        return float(stripped)
    try:
        parsed = parsedate_to_datetime(stripped)
    except TypeError, ValueError:
        return None
    return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def attempt_evidence(
    *,
    number: int,
    started_at: datetime,
    requested_url: str,
    final_url: str | None,
    status_code: int | None,
    media_type: str | None,
    outcome: str,
    failure_code: str | None = None,
    failure_stage: str | None = None,
    failure_message: str | None = None,
    retry_after_seconds: float | None = None,
) -> AcquisitionAttemptEvidence:
    return AcquisitionAttemptEvidence(
        attempt=number,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        response_media_type=media_type,
        outcome=outcome,
        failure_stage=failure_stage,
        failure_code=failure_code,
        failure_message=failure_message,
        retry_after_seconds=retry_after_seconds,
    )


def response_outcome(
    *,
    url: str,
    requested_url: str,
    started: float,
    started_at: datetime,
    attempt_number: int,
    status: int | None,
    media_type: str,
    outcome: ResponseOutcome,
    failure_code: str,
    retry_after: float | None = None,
) -> AcquisitionResult:
    if outcome == "skip":
        evidence = attempt_evidence(
            number=attempt_number,
            started_at=started_at,
            requested_url=requested_url,
            final_url=url,
            status_code=status,
            media_type=media_type,
            outcome="skipped",
            failure_code=failure_code,
        )
        return AcquisitionResult(
            url=url,
            success=True,
            status_code=status,
            duration_seconds=time.perf_counter() - started,
            outcome="skipped",
            response_media_type=media_type,
            attempt_evidence=evidence,
        )
    retryable = outcome == "retry"
    detail = (
        f"Page returned HTTP {status}"
        if failure_code == "http_status"
        else failure_code.replace("_", " ").capitalize()
    )
    evidence = attempt_evidence(
        number=attempt_number,
        started_at=started_at,
        requested_url=requested_url,
        final_url=url,
        status_code=status,
        media_type=media_type,
        outcome="retry" if retryable else "failed",
        failure_stage="navigation",
        failure_code=failure_code,
        failure_message=detail,
        retry_after_seconds=retry_after,
    )
    return AcquisitionResult(
        url=url,
        success=False,
        status_code=status,
        duration_seconds=time.perf_counter() - started,
        error=detail,
        failure_code=failure_code,
        failure_stage="navigation",
        failure_retryable=retryable,
        retry_after_seconds=retry_after,
        outcome="failed",
        response_media_type=media_type,
        attempt_evidence=evidence,
    )


def policy_rejection(
    *,
    url: str,
    requested_url: str,
    started: float,
    started_at: datetime,
    attempt_number: int,
    status: int | None,
    media_type: str,
    retry_after: float | None,
    policy,
) -> AcquisitionResult | None:
    """Return the policy's terminal response, or None when capture may continue."""
    outcome = status_outcome(status, policy)
    if outcome != "accept":
        return response_outcome(
            url=url,
            requested_url=requested_url,
            started=started,
            started_at=started_at,
            attempt_number=attempt_number,
            status=status,
            media_type=media_type,
            outcome=outcome,
            failure_code="http_status",
            retry_after=retry_after,
        )
    if accepted_media_type(media_type, policy.content.accepted_content_types):
        return None
    outcome = policy.content.response_rules.unsupported_content_type
    if outcome == "accept":
        return None
    return response_outcome(
        url=url,
        requested_url=requested_url,
        started=started,
        started_at=started_at,
        attempt_number=attempt_number,
        status=status,
        media_type=media_type,
        outcome=outcome,
        failure_code="unsupported_content_type",
    )


def acquisition_failure(
    url: str,
    started: float,
    started_at: datetime,
    attempt_number: int,
    error: str,
    code: str,
    stage: str,
    retryable: bool,
) -> AcquisitionResult:
    evidence = attempt_evidence(
        number=attempt_number,
        started_at=started_at,
        requested_url=url,
        final_url=None,
        status_code=None,
        media_type=None,
        outcome="retry" if retryable else "failed",
        failure_stage=stage,
        failure_code=code,
        failure_message=error,
    )
    return AcquisitionResult(
        url=url,
        success=False,
        duration_seconds=time.perf_counter() - started,
        error=error,
        failure_code=code,
        failure_stage=stage,
        failure_retryable=retryable,
        outcome="failed",
        attempt_evidence=evidence,
    )
