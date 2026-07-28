"""Failures with process-level or retry semantics at the acquisition boundary."""

from __future__ import annotations

from atlas.crawl.acquisition.models import AcquisitionResult


class RetryableAcquisitionFailure(RuntimeError):
    def __init__(self, result: AcquisitionResult) -> None:
        super().__init__(result.error or "Retryable page acquisition failure")
        self.result = result
        self.retry_after_seconds = result.retry_after_seconds


class PlaywrightRuntimeLost(RuntimeError):
    """The local Playwright driver process or its transport is no longer usable."""


class ExecutionContextReplacedError(RuntimeError):
    pass
