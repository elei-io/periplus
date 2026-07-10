from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from actions.crawl.schemas import CrawlPage
from actions.shared.crawl import CrawlMode, CrawlWait
from crawl_policies.schemas import CrawlPolicyRecord


CalibrationTemplate = Literal[
    "static_fast",
    "static_wait",
    "dynamic_scan",
    "app_stable",
    "app_deep",
]


class Input(BaseModel):
    url: str = Field(description="The representative URL to calibrate crawl transport for.")
    force: bool = Field(
        default=False,
        description="Re-run calibration even when an enabled policy already exists for the broad domain match.",
    )


class CalibrationQuality(BaseModel):
    html_bytes: int = 0
    text_chars: int = 0
    link_count: int = 0
    warning_count: int = 0
    warning_codes: list[str] = Field(default_factory=list)


class CalibrationCandidate(BaseModel):
    template: CalibrationTemplate
    mode: CrawlMode
    wait: CrawlWait
    run_config_overrides: dict[str, Any] = Field(default_factory=dict)
    success: bool
    accepted: bool = False
    reason: str
    duration_seconds: float
    status_code: int | None = None
    quality: CalibrationQuality
    crawl_id: UUID | None = None
    artifact_ids: list[UUID] = Field(default_factory=list)


class CalibrationOutput(BaseModel):
    url: str
    match: str
    url_match_id: UUID
    policy: CrawlPolicyRecord
    reused_policy: bool
    selected_template: CalibrationTemplate
    selected_config: dict[str, Any]
    candidates: list[CalibrationCandidate]
    selected_page: CrawlPage | None = None
