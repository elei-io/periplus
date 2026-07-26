"""Load the deterministic compiler benchmark corpus into ``atlas_load``."""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import date
import json
import os
from queue import Empty, Queue
import threading
import time


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently load the deterministic Atlas compiler benchmark "
            "dataset into the dedicated atlas_load lake."
        )
    )
    parser.add_argument("--lake", choices=("atlas_load",), default="atlas_load")
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=date(2026, 5, 26),
    )
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--crawls-per-day", type=int, default=25_000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the deterministic configuration and expected counts only",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    os.environ["DUCKBASIN_LAKE"] = arguments.lake

    from repository.catalogue import catalogue_from_env
    from repository.catalogue.duckbasin import (
        DuckBasinConfig,
        ServiceAccountTokenProvider,
    )
    from repository.catalogue.operations import (
        is_retryable_catalogue_transaction_conflict,
        is_retryable_catalogue_unavailability,
    )
    from repository.catalogue.synthetic_load import (
        SyntheticLoadConfig,
        current_counts,
        expected_counts,
        is_batch_capacity_pressure,
        load_synthetic_batch,
        prepare_synthetic_catalogue,
        synthetic_batches,
    )

    config = SyntheticLoadConfig(
        start_date=arguments.start_date,
        days=arguments.days,
        crawls_per_day=arguments.crawls_per_day,
        batch_size=arguments.batch_size,
    )
    if arguments.dry_run:
        print(json.dumps(
            {
                "lake": arguments.lake,
                "manifest": config.manifest,
                "expected_counts": expected_counts(config),
                "batches": config.batch_count,
            },
            indent=2,
            sort_keys=True,
        ))
        return

    if arguments.concurrency <= 0 or arguments.concurrency > 8:
        raise SystemExit("--concurrency must be between 1 and 8")
    if arguments.max_batches is not None and arguments.max_batches <= 0:
        raise SystemExit("--max-batches must be greater than zero")

    output_lock = threading.Lock()
    counter_lock = threading.Lock()
    counters = {"loaded": 0, "skipped": 0, "retried": 0}
    started = time.monotonic()

    def emit(event: dict[str, object]) -> None:
        with output_lock:
            print(json.dumps(event, sort_keys=True, default=str), flush=True)

    selected = list(synthetic_batches(config))
    if arguments.max_batches is not None:
        selected = selected[:arguments.max_batches]
    pending: Queue = Queue()
    for batch in selected:
        pending.put(batch)

    tokens = ServiceAccountTokenProvider(DuckBasinConfig.from_env())
    try:
        with catalogue_from_env(
            threads=4,
            memory_limit="4GB",
            tokens=tokens,
        ) as catalogue:
            prepare_synthetic_catalogue(catalogue, config)

        def worker(worker_index: int) -> None:
            delay = 5.0
            while True:
                try:
                    with catalogue_from_env(
                        threads=4,
                        memory_limit="4GB",
                        tokens=tokens,
                    ) as catalogue:
                        session_id = catalogue.session_id
                        while True:
                            try:
                                batch = pending.get_nowait()
                            except Empty:
                                return
                            batch_started = time.monotonic()
                            try:
                                status = load_synthetic_batch(
                                    catalogue,
                                    config,
                                    batch,
                                )
                            except Exception as exc:
                                pending.put(batch)
                                capacity_pressure = (
                                    is_batch_capacity_pressure(exc)
                                )
                                unavailable = (
                                    is_retryable_catalogue_unavailability(exc)
                                )
                                transaction_conflict = (
                                    is_retryable_catalogue_transaction_conflict(
                                        exc
                                    )
                                )
                                if not (
                                    capacity_pressure
                                    or unavailable
                                    or transaction_conflict
                                ):
                                    raise
                                with counter_lock:
                                    counters["retried"] += 1
                                emit({
                                    "status": (
                                        "capacity_retry"
                                        if capacity_pressure
                                        else (
                                            "session_retry"
                                            if unavailable
                                            else "transaction_retry"
                                        )
                                    ),
                                    "worker": worker_index,
                                    "session": session_id,
                                    "batch": batch.index + 1,
                                    "delay_seconds": delay,
                                    "error": str(exc).splitlines()[0],
                                })
                                time.sleep(delay)
                                delay = min(60.0, delay * 2)
                                break
                            else:
                                pending.task_done()
                                delay = 5.0
                                with counter_lock:
                                    counters[status] += 1
                                    completed = (
                                        counters["loaded"] +
                                        counters["skipped"]
                                    )
                                emit({
                                    "status": status,
                                    "worker": worker_index,
                                    "session": session_id,
                                    "batch": batch.index + 1,
                                    "batch_seconds": (
                                        time.monotonic() - batch_started
                                    ),
                                    "completed_batches": completed,
                                    "selected_batches": len(selected),
                                    "elapsed_seconds": (
                                        time.monotonic() - started
                                    ),
                                })
                except Exception:
                    raise

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=arguments.concurrency,
            thread_name_prefix="atlas-load",
        ) as pool:
            futures = [
                pool.submit(worker, index)
                for index in range(arguments.concurrency)
            ]
            for future in concurrent.futures.as_completed(futures):
                future.result()

        with catalogue_from_env(
            threads=4,
            memory_limit="4GB",
            tokens=tokens,
        ) as catalogue:
            counts = current_counts(catalogue)
    finally:
        tokens.close()

    result = {
        "lake": arguments.lake,
        "dataset": config.manifest,
        "expected_counts": expected_counts(config),
        "current_counts": counts,
        "selected_batches": len(selected),
        **counters,
        "elapsed_seconds": time.monotonic() - started,
    }
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
