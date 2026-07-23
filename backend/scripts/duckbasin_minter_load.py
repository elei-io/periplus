"""Exercise the DuckBasin minter with phased, read-only parallel load."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
from pathlib import Path
import statistics
import threading
import time
from typing import Any

import httpx

from repository.catalogue.duckbasin import (
    DuckBasinClientMinter,
    DuckBasinTarget,
    MintedDuckDB,
)


DEFAULT_PHASES = "2:60,5:90,3:90,8:90"
METRIC_PREFIX = "duckbasin_quack_"


@dataclass(frozen=True, slots=True)
class Phase:
    clients: int
    seconds: float


@dataclass(slots=True)
class WorkerResult:
    name: str
    session_id: str = ""
    token_generation: int = 0
    operations: int = 0
    operations_after_token_expiry: int = 0
    latencies: list[float] = field(default_factory=list)
    error: str | None = None


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run read-only, session-affine load through DuckBasin for at "
            "least five minutes by default."
        )
    )
    parser.add_argument(
        "--phases",
        default=DEFAULT_PHASES,
        help="Comma-separated total-client:seconds phases",
    )
    parser.add_argument(
        "--range-rows",
        type=int,
        default=20_000_000,
        help="Rows used by each remote CPU query",
    )
    parser.add_argument("--sample-seconds", type=float, default=2)
    parser.add_argument(
        "--metrics-file",
        type=Path,
        default=Path(".tmp/duckbasin-minter-load.csv"),
    )
    arguments = parser.parse_args()
    arguments.phases = parse_phases(arguments.phases)
    if sum(phase.seconds for phase in arguments.phases) <= 300:
        parser.error("the phase schedule must run for more than five minutes")
    if arguments.range_rows < 1:
        parser.error("--range-rows must be positive")
    if arguments.sample_seconds <= 0:
        parser.error("--sample-seconds must be positive")
    return arguments


def parse_phases(raw: str) -> tuple[Phase, ...]:
    phases: list[Phase] = []
    try:
        for item in raw.split(","):
            clients, seconds = item.split(":", 1)
            phase = Phase(clients=int(clients), seconds=float(seconds))
            if phase.clients < 1 or phase.seconds <= 0:
                raise ValueError
            phases.append(phase)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "phases must be positive total-client:seconds pairs"
        ) from exc
    if not phases:
        raise argparse.ArgumentTypeError("at least one phase is required")
    return tuple(phases)


def run_worker(
    name: str,
    minter: DuckBasinClientMinter,
    deadline: float,
    range_rows: int,
    cancel: threading.Event,
) -> WorkerResult:
    result = WorkerResult(name=name)
    minted: MintedDuckDB | None = None
    try:
        minted = minter.mint()
        result.session_id = minted.session_id
        result.token_generation = minted.token_generation
        remote_sql = (
            "SELECT sum(sqrt(value::DOUBLE)) "
            f"FROM range({range_rows}) AS rows(value)"
        )
        while time.monotonic() < deadline and not cancel.is_set():
            started = time.monotonic()
            minted.connection.execute(
                "FROM quack_query_by_name(?, ?)",
                [minted.catalogue_alias, remote_sql],
            ).fetchone()
            result.latencies.append(time.monotonic() - started)
            result.operations += 1
            if datetime.now(UTC) >= minted.token_expires_at:
                result.operations_after_token_expiry += 1
    except BaseException as exc:
        result.error = f"{exc.__class__.__name__}: {exc}"
        cancel.set()
    finally:
        if minted is not None:
            minted.close()
    return result


def run_phase(
    *,
    index: int,
    phase: Phase,
    minter: DuckBasinClientMinter,
    range_rows: int,
    cancel: threading.Event,
) -> list[WorkerResult]:
    additional_clients = phase.clients - 1
    phase_deadline = time.monotonic() + phase.seconds
    if additional_clients == 0:
        cancel.wait(phase.seconds)
        return []
    threads: list[threading.Thread] = []
    results: list[WorkerResult] = []
    result_lock = threading.Lock()

    def target(worker: int) -> None:
        result = run_worker(
            f"phase-{index}-client-{worker}",
            minter,
            phase_deadline,
            range_rows,
            cancel,
        )
        with result_lock:
            results.append(result)

    for worker in range(additional_clients):
        thread = threading.Thread(target=target, args=(worker,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return results


def monitor(
    *,
    minter: DuckBasinClientMinter,
    target: DuckBasinTarget,
    phase_state: list[str],
    sample_seconds: float,
    output: Path,
    stop: threading.Event,
    samples: list[dict[str, Any]],
) -> None:
    fields = (
        "timestamp",
        "phase",
        "scaling_mode",
        "minimum_replicas",
        "maximum_replicas",
        "desired_replicas",
        "observed_replicas",
        "quack_status",
        "last_scaling_decision",
        "active_requests",
        "queued_requests",
        "active_sessions",
        "ready_replicas",
        "requests_total",
        "token_refreshes",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    gateway_url = _gateway_url(target)
    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        last_print = 0.0
        while not stop.is_set():
            try:
                lake = minter.lake_status()
                metrics = proxy_metrics(
                    gateway_url,
                    target.lake_id.hex,
                    timeout=min(10, sample_seconds),
                )
                row = {
                    "timestamp": datetime.now(UTC).isoformat(),
                    "phase": phase_state[0],
                    "scaling_mode": lake["quack_scaling_mode"],
                    "minimum_replicas": lake["quack_minimum_replicas"],
                    "maximum_replicas": lake["quack_maximum_replicas"],
                    "desired_replicas": lake["quack_desired_replicas"],
                    "observed_replicas": lake["quack_observed_replicas"],
                    "quack_status": lake["quack_status"],
                    "last_scaling_decision": (
                        lake["quack_last_scaling_decision"] or ""
                    ),
                    "active_requests": int(
                        metrics.get("active_requests", 0)
                    ),
                    "queued_requests": int(
                        metrics.get("queued_requests", 0)
                    ),
                    "active_sessions": int(
                        metrics.get("active_sessions", 0)
                    ),
                    "ready_replicas": int(
                        metrics.get("ready_replicas", 0)
                    ),
                    "requests_total": int(
                        metrics.get("requests_total", 0)
                    ),
                    "token_refreshes": minter.tokens.refresh_count,
                }
                samples.append(row)
                writer.writerow(row)
                stream.flush()
                now = time.monotonic()
                if now - last_print >= 10:
                    print(
                        f"[{row['phase']}] replicas="
                        f"{row['observed_replicas']}/"
                        f"{row['desired_replicas']} "
                        f"active={row['active_requests']} "
                        f"queued={row['queued_requests']} "
                        f"sessions={row['active_sessions']} "
                        f"token_generation={row['token_refreshes']}",
                        flush=True,
                    )
                    last_print = now
            except Exception as exc:
                print(f"metrics sample failed: {exc}", flush=True)
            stop.wait(sample_seconds)


def proxy_metrics(
    gateway_url: str,
    lake_hex: str,
    *,
    timeout: float,
) -> dict[str, float]:
    response = httpx.get(f"{gateway_url}/metrics", timeout=timeout)
    response.raise_for_status()
    metrics: dict[str, float] = {}
    lake_label = f'lake_id="{lake_hex}"'
    for line in response.text.splitlines():
        if (
            not line.startswith(METRIC_PREFIX)
            or line.startswith("#")
            or lake_label not in line
        ):
            continue
        series, value = line.rsplit(None, 1)
        name = series.split("{", 1)[0].removeprefix(METRIC_PREFIX)
        if name.endswith(("_bucket", "_sum", "_count")):
            continue
        metrics[name] = metrics.get(name, 0) + float(value)
    return metrics


def summarize(
    *,
    elapsed: float,
    results: list[WorkerResult],
    sentinel: WorkerResult,
    samples: list[dict[str, Any]],
    minter: DuckBasinClientMinter,
) -> None:
    operations = sum(result.operations for result in results)
    latencies = [
        latency for result in results for latency in result.latencies
    ]
    errors = [result.error for result in results if result.error is not None]
    decisions = sorted(
        {
            str(sample["last_scaling_decision"])
            for sample in samples
            if sample["last_scaling_decision"]
        }
    )
    print("\nDuckBasin minter load result")
    print(f"elapsed_seconds={elapsed:.1f}")
    print(f"operations={operations}")
    print(f"errors={len(errors)}")
    print(f"token_refreshes={minter.tokens.refresh_count}")
    print(
        "sentinel_operations_after_original_token_expiry="
        f"{sentinel.operations_after_token_expiry}"
    )
    if latencies:
        print(f"latency_p50_seconds={statistics.median(latencies):.3f}")
        print(f"latency_p95_seconds={percentile(latencies, 95):.3f}")
    if samples:
        print(
            "observed_replicas_range="
            f"{min(int(sample['observed_replicas']) for sample in samples)}-"
            f"{max(int(sample['observed_replicas']) for sample in samples)}"
        )
        print(
            "desired_replicas_range="
            f"{min(int(sample['desired_replicas']) for sample in samples)}-"
            f"{max(int(sample['desired_replicas']) for sample in samples)}"
        )
        print(
            "max_queued_requests="
            f"{max(int(sample['queued_requests']) for sample in samples)}"
        )
        print(
            "max_active_sessions="
            f"{max(int(sample['active_sessions']) for sample in samples)}"
        )
    print(f"scaling_decisions={decisions}")
    for error in errors:
        print(f"worker_error={error}")


def percentile(values: list[float], percent: int) -> float:
    ordered = sorted(values)
    index = math.ceil((len(ordered) - 1) * percent / 100)
    return ordered[index]


def _gateway_url(target: DuckBasinTarget) -> str:
    scheme = "http" if target.disable_ssl else "https"
    return f"{scheme}://{target.quack_uri.removeprefix('quack:')}"


def main() -> None:
    arguments = parse_arguments()
    phases: tuple[Phase, ...] = arguments.phases
    total_seconds = sum(phase.seconds for phase in phases)
    print(
        f"Starting {total_seconds:g}s read-only load with phases "
        + ", ".join(
            f"{phase.clients} clients/{phase.seconds:g}s"
            for phase in phases
        ),
        flush=True,
    )
    cancel = threading.Event()
    monitor_stop = threading.Event()
    phase_state = ["cold-start"]
    samples: list[dict[str, Any]] = []
    results: list[WorkerResult] = []
    with DuckBasinClientMinter() as minter:
        target = minter.target()
        initial = minter.lake_status()
        print(
            "Basin scaling="
            f"{initial['quack_scaling_mode']} "
            f"{initial['quack_minimum_replicas']}-"
            f"{initial['quack_maximum_replicas']} "
            f"initial={initial['quack_observed_replicas']}/"
            f"{initial['quack_desired_replicas']}",
            flush=True,
        )
        monitor_thread = threading.Thread(
            target=monitor,
            kwargs={
                "minter": minter,
                "target": target,
                "phase_state": phase_state,
                "sample_seconds": arguments.sample_seconds,
                "output": arguments.metrics_file,
                "stop": monitor_stop,
                "samples": samples,
            },
            daemon=True,
        )
        monitor_thread.start()
        started = time.monotonic()
        sentinel_result: list[WorkerResult] = []

        def sentinel_target() -> None:
            sentinel_result.append(
                run_worker(
                    "sentinel",
                    minter,
                    started + total_seconds,
                    arguments.range_rows,
                    cancel,
                )
            )

        sentinel_thread = threading.Thread(
            target=sentinel_target,
            daemon=True,
        )
        sentinel_thread.start()
        try:
            for index, phase in enumerate(phases, start=1):
                if cancel.is_set():
                    break
                phase_state[0] = (
                    f"phase-{index}:{phase.clients}-clients"
                )
                results.extend(
                    run_phase(
                        index=index,
                        phase=phase,
                        minter=minter,
                        range_rows=arguments.range_rows,
                        cancel=cancel,
                    )
                )
            sentinel_thread.join()
        except KeyboardInterrupt:
            cancel.set()
            sentinel_thread.join()
        finally:
            monitor_stop.set()
            monitor_thread.join(timeout=arguments.sample_seconds + 10)
        elapsed = time.monotonic() - started
        if not sentinel_result:
            raise SystemExit("sentinel did not return a result")
        sentinel = sentinel_result[0]
        results.append(sentinel)
        summarize(
            elapsed=elapsed,
            results=results,
            sentinel=sentinel,
            samples=samples,
            minter=minter,
        )
        print(f"metrics_file={arguments.metrics_file.resolve()}")
        if any(result.error for result in results):
            raise SystemExit(1)
        if minter.tokens.refresh_count < 2:
            raise SystemExit("token rollover was not exercised")
        if sentinel.operations_after_token_expiry < 1:
            raise SystemExit(
                "the original session did not survive beyond token expiry"
            )


if __name__ == "__main__":
    main()
