"""Frozen acquisition input, independent of collection and traversal ownership."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from periplus.crawl.control.collections.exclusions import UrlExclusion
from periplus.crawl.acquisition.models import AcquisitionAttemptEvidence, AcquisitionStepEvidence
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot


class AcquisitionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_id: UUID
    admitted_at: datetime
    policy: EffectivePolicySnapshot
    attempt_reserved_ms: int = Field(default=125000, ge=0, le=3605000)
    dispatch_policy_version: int = Field(default=1, ge=1)
    exclusions: tuple[UrlExclusion, ...] = ()
    exclusion_policy_version: int = Field(default=1, ge=1)
    prior_attempts: tuple[AcquisitionAttemptEvidence, ...] = ()
    prior_steps: tuple[AcquisitionStepEvidence, ...] = ()
