"""Low-cardinality metrics for deployment-wide resource admission."""

from prometheus_client import Counter, Histogram


_requests = Counter(
    "atlas_resource_admission_requests_total",
    "Resource admission outcomes by service class and resource family.",
    ("service_class", "resource", "outcome"),
)
_wait = Histogram(
    "atlas_resource_admission_wait_seconds",
    "Time spent waiting for a resource bundle.",
    ("service_class", "resource", "outcome"),
)
_hold = Histogram(
    "atlas_resource_admission_hold_seconds",
    "Time a granted resource bundle remained held.",
    ("service_class", "resource", "outcome"),
)


def _family(name: str) -> str:
    return "remote" if name.startswith("remote:") else name


def admission(request, *, outcome: str, wait_seconds: float) -> None:
    for resource in request.resources:
        family = _family(resource.name)
        _requests.labels(request.service_class, family, outcome).inc()
        _wait.labels(request.service_class, family, outcome).observe(
            max(0.0, wait_seconds)
        )


def hold(request, *, outcome: str, hold_seconds: float) -> None:
    for resource in request.resources:
        _hold.labels(request.service_class, _family(resource.name), outcome).observe(
            max(0.0, hold_seconds)
        )
