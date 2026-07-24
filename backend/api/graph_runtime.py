"""Process-owned graph runtime handles for API adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request


@dataclass(frozen=True, slots=True)
class ApiGraphRuntime:
    """NATS handles reconciled once during API process startup."""

    nats_client: Any
    jetstream: Any
    runs: Any
    requests: Any
    workers: Any
    catalogue_workers: Any


def get_graph_runtime(request: Request) -> ApiGraphRuntime:
    runtime = getattr(request.app.state, "graph_runtime", None)
    if not isinstance(runtime, ApiGraphRuntime):
        raise RuntimeError("API graph runtime is unavailable.")
    return runtime
