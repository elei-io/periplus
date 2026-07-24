#!/usr/bin/env python3
"""Exercise two writer replicas and kill active workers before settlement."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import nats
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
    subprocess.run(["docker", "compose", *arguments], cwd=ROOT, check=True)


def containers(service: str) -> list[str]:
    output = subprocess.run(
        ["docker", "compose", "ps", "-q", service],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [value for value in output.splitlines() if value]


def healthy_replicas(service: str) -> int:
    count = 0
    for container_id in containers(service):
        status = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        count += status == "healthy"
    return count


def kill_one(service: str) -> str:
    values = containers(service)
    if len(values) < 2:
        raise RuntimeError(f"{service} does not have two replicas")
    subprocess.run(["docker", "kill", values[0]], check=True, capture_output=True)
    return values[0][:12]


async def consumer_state(stream: str, durable: str) -> dict[str, int]:
    client = await nats.connect("nats://127.0.0.1:4222", connect_timeout=2)
    try:
        info = await client.jetstream().consumer_info(stream, durable)
        return {
            "pending": info.num_pending,
            "ack_pending": info.num_ack_pending,
            "redelivered": info.num_redelivered,
        }
    finally:
        await client.close()


def wait_for_consumer_activity(
    consumers: list[tuple[str, str]], timeout: float = 30
) -> dict[str, dict[str, int]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        states = {
            durable: asyncio.run(consumer_state(stream, durable))
            for stream, durable in consumers
        }
        if any(value["ack_pending"] > 0 for value in states.values()):
            return states
        time.sleep(0.2)
    raise RuntimeError(f"no consumer claimed work within {timeout:g}s")


def wait_for_run(run_id: str, timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            run = api("GET", f"/graph-runs/{run_id}")
        except TimeoutError:
            time.sleep(1)
            continue
        if run["status"] in TERMINAL_RUN_STATES:
            return run
        time.sleep(1)
    raise RuntimeError(f"graph run {run_id} did not become terminal")


def wait_for_materialization(run_id: str, timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lag = materialization_lag(run_id)
        except (TimeoutError, RuntimeError):
            # A remote catalogue read may briefly fail while the deployment
            # recovers from the deliberately killed worker.
            time.sleep(1)
            continue
        if lag["pending_updates"] == 0 and lag["failed_updates"] == 0:
            return lag
        time.sleep(1)
    raise RuntimeError(f"materialization did not settle for graph run {run_id}")


def wait_for_replica_health(service: str, expected: int, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy_replicas(service) == expected:
            return
        time.sleep(1)
    raise RuntimeError(f"{service} did not reach {expected} healthy replicas")


def wait_for_replicas(service: str, expected: int, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(containers(service)) == expected:
            return
        time.sleep(0.25)
    raise RuntimeError(f"{service} did not start {expected} replicas")


def main() -> None:
    token = uuid4().hex
    materialization_fixture = ensure_active_materialization(token)
    graph_id: str | None = None
    killed_ingestion = None
    killed_materialization = None
    try:
        compose(
            "up",
            "-d",
            "--wait",
            "--scale",
            "atlas-ingestion-worker=2",
            "--scale",
            "atlas-materialization-worker=2",
            "atlas-ingestion-worker",
            "atlas-materialization-worker",
        )
        wait_for_replica_health("atlas-ingestion-worker", 2)
        wait_for_replica_health("atlas-materialization-worker", 2)

        graph = api(
            "POST",
            "/crawl-graphs/",
            {
                "slug": f"worker-fencing-{token[:8]}",
                "description": "Disposable Phase 3 multi-replica and redelivery proof",
            },
        )
        graph_id = graph["id"]
        node = api("POST", f"/crawl-graphs/{graph_id}/nodes", {"name": "Root"})
        api(
            "PUT",
            f"/crawl-graphs/{graph_id}",
            {
                "slug": graph["slug"],
                "description": graph["description"],
                "root_node_id": node["id"],
            },
        )

        urls = [
            f"https://example.com/?atlas-phase3={token}-{index}"
            for index in range(8)
        ]
        submission = api("POST", f"/crawl-graphs/{graph_id}/runs", {"urls": urls})
        run_id = submission["run_id"]
        ingestion_before_kill = wait_for_consumer_activity(
            [("ATLAS_CATALOGUE_WORK", "atlas-repository-writer")], timeout=120
        )
        require_healthy("atlas-ingestion-worker", expected=2)
        require_healthy("atlas-materialization-worker", expected=2)
        killed_ingestion = kill_one("atlas-ingestion-worker")

        materialization_before_kill = wait_for_consumer_activity(
            [("ATLAS_CATALOGUE_WORK", "atlas-materialization-live-worker")],
            timeout=120,
        )
        killed_materialization = kill_one("atlas-materialization-worker")

        completed = wait_for_run(run_id)
        if (
            completed["status"] != "completed"
            or completed["failed_request_count"] != 0
            or completed["warning_count"] != 0
            or completed["error_count"] != 0
            or completed["request_count"] != len(urls)
        ):
            raise RuntimeError(f"ingestion did not settle exactly once: {completed}")
        settled_lag = wait_for_materialization(run_id)
        stable = api("GET", f"/graph-runs/{run_id}")
        if stable["request_count"] != len(urls) or stable["completed_at"] != completed["completed_at"]:
            raise RuntimeError("materialization recovery changed or reacquired the graph run")

        # `docker kill` deliberately leaves the victim stopped. Recreate both
        # replica sets only after the surviving replicas have settled all work.
        compose(
            "up",
            "-d",
            "--wait",
            "--scale",
            "atlas-ingestion-worker=2",
            "--scale",
            "atlas-materialization-worker=2",
            "atlas-ingestion-worker",
            "atlas-materialization-worker",
        )
        wait_for_replica_health("atlas-ingestion-worker", 2)
        wait_for_replica_health("atlas-materialization-worker", 2)
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "requests_settled_once": completed["request_count"],
                    "ingestion_replicas": 2,
                    "materialization_replicas": 2,
                    "ingestion_before_kill": ingestion_before_kill,
                    "materialization_before_kill": materialization_before_kill,
                    "killed_ingestion_container": killed_ingestion,
                    "killed_materialization_container": killed_materialization,
                    "settled_lag": settled_lag,
                    "reacquired": False,
                },
                indent=2,
            )
        )
    except BaseException:
        destination = capture_diagnostics("worker-horizontal-safety")
        print(f"reliability diagnostics: {destination}")
        raise
    finally:
        try:
            compose(
                "up",
                "-d",
                "--scale",
                "atlas-ingestion-worker=1",
                "--scale",
                "atlas-materialization-worker=1",
                "atlas-acquisition-worker",
                "atlas-ingestion-worker",
                "atlas-materialization-worker",
                "atlas-housekeeping-worker",
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
