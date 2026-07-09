from pydantic import BaseModel, Field


class MetricCard(BaseModel):
    metric: str
    label: str
    value: float | int | str
    unit: str | None = None
    tone: str | None = None
    description: str | None = None


class MetricDatum(BaseModel):
    metric: str
    label: str
    value: float | int
    labels: dict[str, str] = Field(default_factory=dict)


class MetricBreakdown(BaseModel):
    metric: str
    label: str
    unit: str | None = None
    items: list[MetricDatum] = Field(default_factory=list)


class HistoryMetricsResponse(BaseModel):
    window_seconds: int
    cards: list[MetricCard] = Field(default_factory=list)
    breakdowns: list[MetricBreakdown] = Field(default_factory=list)
