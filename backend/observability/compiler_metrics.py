"""Privacy-safe catalogue compiler coverage metrics."""

from prometheus_client import Counter, Histogram


_compilations = Counter(
    "atlas_catalogue_compilations_total",
    "Catalogue compiler decisions by bounded classification.",
    (
        "source",
        "purpose",
        "outcome",
        "diagnostic_category",
        "compiler_version",
    ),
)
_diagnostics = Counter(
    "atlas_catalogue_compilation_diagnostics_total",
    "Catalogue compiler diagnostics by stable code and category.",
    ("purpose", "category", "code"),
)
_rewrites = Counter(
    "atlas_catalogue_compilation_rewrites_total",
    "Catalogue compiler rewrites by stable rule.",
    ("purpose", "rule"),
)
_duration = Histogram(
    "atlas_catalogue_compilation_duration_seconds",
    "Catalogue compilation latency.",
    ("purpose", "outcome"),
)
_defects = Counter(
    "atlas_catalogue_compiler_defects_total",
    "Unexpected catalogue compiler defects.",
    ("source", "purpose", "exception_type"),
)


def completed(
    *,
    source: str,
    purpose: str,
    outcome: str,
    diagnostic_category: str,
    compiler_version: str,
    duration_seconds: float,
    diagnostics: tuple[tuple[str, str], ...],
    rewrite_rules: tuple[str, ...],
) -> None:
    _compilations.labels(
        source,
        purpose,
        outcome,
        diagnostic_category,
        compiler_version,
    ).inc()
    _duration.labels(purpose, outcome).observe(max(0.0, duration_seconds))
    for category, code in diagnostics:
        _diagnostics.labels(purpose, category, code).inc()
    for rule in rewrite_rules:
        _rewrites.labels(purpose, rule).inc()


def defect(
    *,
    source: str,
    purpose: str,
    exception_type: str,
) -> None:
    _defects.labels(source, purpose, exception_type).inc()
