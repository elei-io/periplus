from typing import Any

from pydantic import BaseModel, Field


class QualityWarningSignal(BaseModel):
    name: str
    value: Any


class QualityWarning(BaseModel):
    code: str
    name: str
    description: str
    signals: list[QualityWarningSignal] = Field(default_factory=list)
