#!/usr/bin/env python3
"""Exercise two writer replicas and kill active workers before settlement."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import nats
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    catalogue_request,
    ensure_resource_governor_storage,
    resource_permits,
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


def run_lag(run_id: str) -> dict[str, Any]:
    response = api("GET", "/graph-runs/materialization-lag")
    return next(
        (item for item in response["items"] if item["run_id"] == run_id),
        {"pending_updates": 0, "failed_updates": 0, "materialization_count": 0},
    )


def wait_for_materialization(run_id: str, timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lag = run_lag(run_id)
        except (TimeoutError, RuntimeError) as exc:
            # Operational reads may briefly return 503 while object storage or
            # DuckLake metadata reconnects during a killed worker's handoff.
            if isinstance(exc, RuntimeError) and "503" not in str(exc):
                raise
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


def hold_catalogue_capacity(seconds: float) -> tuple[Thread, Event, list[BaseException]]:
    acquired = Event()
    errors: list[BaseException] = []

    async def hold() -> None:
        client = await nats.connect("nats://127.0.0.1:4222", connect_timeout=2)
        try:
            bucket = await ensure_resource_governor_storage(client.jetstream())
            async with resource_permits(
                bucket,
                catalogue_request(
                    f"horizontal-smoke-hold-{uuid4().hex}",
                    service_class="maintenance",
                    exclusive=True,
                ),
                acquire_timeout=DURABLE_RESOURCE_WAIT,
            ):
                acquired.set()
                await asyncio.sleep(seconds)
        finally:
            await client.close()

    def run() -> None:
        try:
            asyncio.run(hold())
        except BaseException as exc:
            errors.append(exc)
            acquired.set()

    thread = Thread(target=run, daemon=True)
    thread.start()
    return thread, acquired, errors


def wait_for_catalogue_waiter(service_class: str, timeout: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    field = f"{service_class}_waiting"
    while time.monotonic() < deadline:
        capacity = api("GET", "/graph-runs/capacity")
        catalogue = next(
            item for item in capacity["resources"] if item["name"] == "catalogue:hot"
        )
        if catalogue[field] > 0:
            return catalogue
        time.sleep(0.2)
    raise RuntimeError(f"no {service_class} catalogue waiter appeared")


def main() -> None:
    materializations = api("GET", "/catalogue/materializations/")
    if not materializations["items"]:
        raise RuntimeError("the horizontal smoke requires one active materialized view")

    token = uuid4().hex
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
                "name": f"Worker fencing {token[:8]}",
                "description": "Disposable Phase 3 multi-replica and redelivery proof",
            },
        )
        graph_id = graph["id"]
        node = api("POST", f"/crawl-graphs/{graph_id}/nodes", {"name": "Root"})
        api(
            "PUT",
            f"/crawl-graphs/{graph_id}",
            {
                "name": graph["name"],
                "description": graph["description"],
                "root_node_id": node["id"],
            },
        )

        # Hold the complete catalogue budget beyond one ACK interval. Both
        # catalogue capabilities remain online so the queue, heartbeats, and
        # work-conserving handoff are exercised under real contention.
        holder, holder_acquired, holder_errors = hold_catalogue_capacity(65)
        if not holder_acquired.wait(timeout=30):
            raise RuntimeError("catalogue saturation permit was not acquired")
        if holder_errors:
            raise holder_errors[0]
        urls = [f"https://example.com/?atlas-phase3={token}-{index}" for index in range(48)]
        submission = api("POST", f"/crawl-graphs/{graph_id}/runs", {"urls": urls})
        run_id = submission["run_id"]
        ingestion_before_kill = wait_for_consumer_activity(
            [("ATLAS_CATALOGUE_WORK", "atlas-repository-writer")], timeout=60
        )
        catalogue_wait = wait_for_catalogue_waiter("critical")
        killed_ingestion = kill_one("atlas-ingestion-worker")
        holder.join(timeout=90)
        if holder.is_alive():
            raise RuntimeError("catalogue saturation permit did not release")
        if holder_errors:
            raise holder_errors[0]

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
                    "catalogue_wait": catalogue_wait,
                    "settled_lag": settled_lag,
                    "reacquired": False,
                },
                indent=2,
            )
        )
    finally:
        compose(
            "up",
            "-d",
            "--wait",
            "--scale",
            "atlas-ingestion-worker=1",
            "--scale",
            "atlas-materialization-worker=1",
            "atlas-crawl-http-worker",
            "atlas-crawl-browser-worker",
            "atlas-crawl-provider-worker",
            "atlas-ingestion-worker",
            "atlas-materialization-worker",
        )
        if graph_id is not None:
            try:
                api("DELETE", f"/crawl-graphs/{graph_id}")
            except Exception as exc:
                print(f"warning: disposable smoke graph cleanup failed: {exc}")


if __name__ == "__main__":
    main()
