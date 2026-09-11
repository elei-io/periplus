"""Deterministic attempt and completion-step records for frozen acquisition evidence."""
from uuid import UUID
from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionStepEvidence
from periplus.platform.catalogue.records import AttemptRecord, StepRecord, attempt_id_for
from periplus.urls import normalize_url


def attempt_records(identity: UUID, attempt_evidence: tuple[AcquisitionAttemptEvidence, ...]) -> tuple[AttemptRecord, ...]:
    return tuple(
        AttemptRecord(
            attempt_id=attempt_id_for(identity, index),
            visit_id=identity,
            attempt_index=index,
            resource_usage=attempt.resource_usage,
            started_at=attempt.started_at,
            finished_at=attempt.completed_at,
            effective_url=(
                normalize_url(attempt.final_url)
                if attempt.final_url is not None
                else None
            ),
            status_code=attempt.status_code,
            outcome=(
                "uncertain" if attempt.outcome == "uncertain" else "succeeded"
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
                (attempt.failure_message or attempt.failure_code or "")[:2048] or None
                if attempt.outcome in {"retry", "failed"}
                else None
            ),
        )
        for index, attempt in enumerate(attempt_evidence)
    )

def step_records(identity: UUID, step_evidence: tuple[AcquisitionStepEvidence, ...]) -> tuple[StepRecord, ...]:
    return tuple(
        StepRecord(
            attempt_id=attempt_id_for(
                identity,
                step.attempt_number - 1,
            ),
            step_index=step.step_ordinal - 1,
            action=step.method,
            parameters={
                "action_version": step.method_version,
                "config": step.config_json,
                "measurements": {
                    "iterations": step.iterations,
                    "before": {"elements": step.before_element_count, "text_chars": step.before_text_chars,
                               "links": step.before_link_count, "scroll_height": step.before_scroll_height},
                    "after": {"elements": step.after_element_count, "text_chars": step.after_text_chars,
                              "links": step.after_link_count, "scroll_height": step.after_scroll_height},
                },
            },
            started_at=step.started_at,
            duration_ms=step.duration_ms,
            outcome="succeeded",
            stopping_reason=step.stop_reason,
        )
        for step in step_evidence
    )
