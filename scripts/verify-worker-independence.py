#!/usr/bin/env python3
"""Prove ingestion and materialization are independent local deployments."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from reliability_support import (
    capture_diagnostics,
    cleanup_materialization_fixture,
    ensure_active_materialization,
    materialization_lag,
    require_healthy,
)


ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"
TERMINAL_RUN_STATES = {"completed", "completed_with_errors", "failed", "cancelled"}


def api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read()
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc
    return json.loads(body) if body else None


def compose(*arguments: str) -> None:
    subprocess.run(
        ["docker", "compose", *arguments],
        cwd=ROOT,
        check=True,
    )


def container_health(service: str) -> str:
    container_id = subprocess.run(
        ["docker", "compose", "ps", "-q", service],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not container_id:
        return "missing"
    return subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def wait_for_run(run_id: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api("GET", f"/graph-runs/{run_id}")
        if run["status"] in TERMINAL_RUN_STATES:
            return run
        time.sleep(1)
    raise RuntimeError(f"graph run {run_id} did not become terminal")


def wait_for_materialization(run_id: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lag = materialization_lag(run_id)
        if lag["pending_updates"] == 0 and lag["failed_updates"] == 0:
            return lag
        time.sleep(1)
    raise RuntimeError(f"materialization did not catch up for graph run {run_id}")


def main() -> None:
    token = uuid4().hex
    materialization_fixture = ensure_active_materialization(token)
    graph_id: str | None = None
    try:
        graph = api(
            "POST",
            "/crawl-graphs/",
            {
                "slug": f"worker-independence-{token[:8]}",
                "description": "Disposable Phase 2 worker-independence smoke graph",
            },
        )
        graph_id = graph["id"]
        node = api(
            "POST",
            f"/crawl-graphs/{graph_id}/nodes",
            {"name": "Root"},
        )
        api(
            "PUT",
            f"/crawl-graphs/{graph_id}",
            {
                "slug": graph["slug"],
                "description": graph["description"],
                "root_node_id": node["id"],
            },
        )

        compose("stop", "atlas-materialization-worker")
        require_healthy("atlas-acquisition-worker")
        require_healthy("atlas-ingestion-worker")
        submission = api(
            "POST",
            f"/crawl-graphs/{graph_id}/runs",
            {"urls": [f"https://example.com/?atlas-phase2={token}"]},
        )
        run_id = submission["run_id"]
        completed = wait_for_run(run_id)
        if completed["status"] != "completed" or completed["failed_request_count"]:
            raise RuntimeError(
                f"graph did not complete cleanly with materialization offline: {completed}"
            )
        require_healthy("atlas-acquisition-worker")
        require_healthy("atlas-ingestion-worker")
        offline_lag = materialization_lag(run_id)
        if (
            offline_lag["materialization_count"] < 1
            or offline_lag["pending_updates"] < 1
        ):
            raise RuntimeError(
                f"offline materialization backlog is not visible: {offline_lag}"
            )

        stable_run = {
            "request_count": completed["request_count"],
            "completed_at": completed["completed_at"],
        }
        compose("up", "-d", "--wait", "atlas-materialization-worker")
        require_healthy("atlas-materialization-worker", expected=1)
        settled_lag = wait_for_materialization(run_id)
        after = api("GET", f"/graph-runs/{run_id}")
        if stable_run != {
            "request_count": after["request_count"],
            "completed_at": after["completed_at"],
        }:
            raise RuntimeError("materialization recovery reacquired or changed the graph run")

        compose("stop", "atlas-ingestion-worker")
        if container_health("atlas-materialization-worker") != "healthy":
            raise RuntimeError("materialization became unhealthy when ingestion stopped")

        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "graph_status": completed["status"],
                    "offline_lag": offline_lag,
                    "settled_lag": settled_lag,
                    "reacquired": False,
                    "materialization_health_without_ingestion": "healthy",
                },
                indent=2,
            )
        )
    except BaseException:
        destination = capture_diagnostics("worker-independence")
        print(f"reliability diagnostics: {destination}")
        raise
    finally:
        try:
            compose(
                "up",
                "-d",
                "atlas-ingestion-worker",
                "atlas-materialization-worker",
            )
        except Exception as exc:
            print(f"warning: worker restoration failed: {exc}")
        if graph_id is not None:
            try:
                api("DELETE", f"/crawl-graphs/{graph_id}")
            except Exception as exc:
                print(f"warning: disposable smoke graph cleanup failed: {exc}")
        try:
            cleanup_materialization_fixture(materialization_fixture)
        except Exception as exc:
            print(f"warning: disposable materialization cleanup failed: {exc}")


if __name__ == "__main__":
    main()
