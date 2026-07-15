#!/usr/bin/env python3
"""Prove browser replicas can disappear without changing HTTP capacity."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


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
        raise RuntimeError(f"{method} {path} failed: {exc.code} {exc.read().decode()}") from exc
    return json.loads(body) if body else None


def compose(*arguments: str) -> None:
    subprocess.run(["docker", "compose", *arguments], cwd=ROOT, check=True)


def transport(capacity: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in capacity["transports"] if item["transport"] == name)


def provisioned(value: dict[str, Any]) -> tuple[int, int]:
    return value["worker_count"], value["capacity"]


def wait_for_capacity(name: str, worker_count: int, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = api("GET", "/graph-runs/capacity")
        if transport(current, name)["worker_count"] == worker_count:
            return current
        time.sleep(1)
    raise RuntimeError(f"{name} worker count did not become {worker_count}")


def wait_for_run(run_id: str, timeout: float = 90) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api("GET", f"/graph-runs/{run_id}")
        if run["status"] in TERMINAL_RUN_STATES:
            return run
        time.sleep(1)
    raise RuntimeError(f"graph run {run_id} did not become terminal")


def main() -> None:
    baseline = api("GET", "/graph-runs/capacity")
    baseline_http = transport(baseline, "http")
    baseline_browser = transport(baseline, "browser")
    if baseline_http["worker_count"] < 1 or baseline_browser["worker_count"] < 1:
        raise RuntimeError(f"transport workers are not online: {baseline['transports']}")

    graph_id: str | None = None
    try:
        compose("stop", "atlas-crawl-browser-worker")
        without_browser = wait_for_capacity("browser", 0)
        if provisioned(transport(without_browser, "http")) != provisioned(baseline_http):
            raise RuntimeError("stopping browser workers changed HTTP capacity")

        token = uuid4().hex
        graph = api(
            "POST",
            "/crawl-graphs/",
            {"name": f"Transport independence {token[:8]}", "description": "Phase 4 smoke"},
        )
        graph_id = graph["id"]
        node = api("POST", f"/crawl-graphs/{graph_id}/nodes", {"name": "Root"})
        api(
            "PUT",
            f"/crawl-graphs/{graph_id}",
            {"name": graph["name"], "description": graph["description"], "root_node_id": node["id"]},
        )
        submission = api(
            "POST",
            f"/crawl-graphs/{graph_id}/runs",
            {"urls": [f"https://example.com/?atlas-phase4={token}"]},
        )
        completed = wait_for_run(submission["run_id"])
        if completed["status"] != "completed" or completed["failed_request_count"]:
            raise RuntimeError(f"HTTP acquisition failed without browser workers: {completed}")

        compose("up", "-d", "atlas-crawl-browser-worker")
        restored = wait_for_capacity("browser", baseline_browser["worker_count"])
        if provisioned(transport(restored, "http")) != provisioned(baseline_http):
            raise RuntimeError("starting browser workers changed HTTP capacity")
        print(json.dumps({
            "run_id": submission["run_id"],
            "http_run_without_browser": completed["status"],
            "http_capacity": baseline_http,
            "browser_capacity_before": baseline_browser,
            "browser_capacity_while_stopped": transport(without_browser, "browser"),
            "browser_capacity_restored": transport(restored, "browser"),
        }, indent=2))
    finally:
        compose("up", "-d", "atlas-crawl-browser-worker")
        if graph_id is not None:
            try:
                api("DELETE", f"/crawl-graphs/{graph_id}")
            except Exception as exc:
                print(f"warning: disposable smoke graph cleanup failed: {exc}")


if __name__ == "__main__":
    main()
