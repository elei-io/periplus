from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, JsonValue


class AcquisitionStepEvidence(BaseModel):
    attempt_number: int
    step_ordinal: int
    method: Literal["wait_dynamic", "wait_fixed", "scroll", "expand"]
    method_version: int = 1
    config_hash: str
    config_json: dict[str, JsonValue]
    started_at: datetime
    duration_ms: int
    iterations: int
    stop_reason: str
    before_element_count: int
    after_element_count: int
    before_text_chars: int
    after_text_chars: int
    before_link_count: int
    after_link_count: int
    before_scroll_height: int
    after_scroll_height: int

    @property
    def changed(self) -> bool:
        return any(
            before != after
            for before, after in (
                (self.before_element_count, self.after_element_count),
                (self.before_text_chars, self.after_text_chars),
                (self.before_link_count, self.after_link_count),
                (self.before_scroll_height, self.after_scroll_height),
            )
        )


class AcquisitionAttemptEvidence(BaseModel):
    attempt: int
    started_at: datetime
    completed_at: datetime
    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    response_media_type: str | None = None
    outcome: Literal["success", "retry", "failed", "skipped"]
    failure_stage: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    retry_after_seconds: float | None = None


class AcquisitionResult(BaseModel):
    url: str
    success: bool
    status_code: int | None = None
    duration_seconds: float
    visit_id: UUID | None = None
    document_id: UUID | None = None
    content_sha256: str | None = None
    repository_snapshot: int | None = None
    repository_crawl_created: bool | None = None
    html: str | None = None
    crawl: dict[str, Any] | None = None
    error: str | None = None
    failure_code: str | None = None
    failure_stage: str | None = None
    failure_retryable: bool | None = None
    retry_after_seconds: float | None = None
    outcome: Literal["success", "failed", "skipped"] = "success"
    response_media_type: str | None = None
    document_bytes: bytes | None = None
    attempt_evidence: AcquisitionAttemptEvidence | None = None
    steps: tuple[AcquisitionStepEvidence, ...] = ()
