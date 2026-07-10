from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    ppid: int
    command: str


@dataclass(frozen=True)
class PlaywrightCleanupResult:
    drivers_found: int
    processes_signalled: int
    browsers_found: int = 0


def playwright_cleanup_interval_seconds() -> int:
    try:
        return max(
            1,
            int(os.getenv("ATLAS_PLAYWRIGHT_CLEANUP_INTERVAL_SECONDS", "300")),
        )
    except ValueError:
        return 300


def _driver_marker() -> str:
    try:
        import playwright

        return str(Path(playwright.__file__).resolve().parent / "driver")
    except (ImportError, TypeError):
        return "playwright/driver"


def _processes() -> list[ProcessSnapshot]:
    result = subprocess.run(
        ["ps", "-ww", "-axo", "pid=,ppid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    processes: list[ProcessSnapshot] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        processes.append(ProcessSnapshot(pid=pid, ppid=ppid, command=fields[2]))
    return processes


def _is_worker_command(command: str) -> bool:
    return "worker.app" in command or "atlas-worker" in command


def _has_worker_ancestor(
    process: ProcessSnapshot,
    by_pid: dict[int, ProcessSnapshot],
) -> bool:
    seen = {process.pid}
    current = process
    while current.ppid > 1 and current.ppid not in seen:
        seen.add(current.ppid)
        parent = by_pid.get(current.ppid)
        if parent is None:
            return False
        if _is_worker_command(parent.command):
            return True
        current = parent
    return False


def _has_task_child_ancestor(
    process: ProcessSnapshot,
    by_pid: dict[int, ProcessSnapshot],
) -> bool:
    seen = {process.pid}
    current = process
    while current.ppid > 1 and current.ppid not in seen:
        seen.add(current.ppid)
        parent = by_pid.get(current.ppid)
        if parent is None:
            return False
        if "multiprocessing.spawn" in parent.command:
            return True
        current = parent
    return False


def find_orphaned_playwright_drivers(
    processes: list[ProcessSnapshot],
    *,
    driver_marker: str | None = None,
) -> list[ProcessSnapshot]:
    marker = driver_marker or _driver_marker()
    by_pid = {process.pid: process for process in processes}
    return [
        process
        for process in processes
        if marker in process.command
        and "run-driver" in process.command
        and _has_task_child_ancestor(process, by_pid)
        and not _has_worker_ancestor(process, by_pid)
    ]


def find_detached_playwright_browsers(
    processes: list[ProcessSnapshot],
) -> list[ProcessSnapshot]:
    return [
        process
        for process in processes
        if process.ppid == 1
        and "Google Chrome for Testing" in process.command
        and "playwright_chromiumdev_profile-" in process.command
        and "--type=" not in process.command
    ]


def _tree_pids(root_pid: int, processes: list[ProcessSnapshot]) -> list[int]:
    children: dict[int, list[int]] = {}
    for process in processes:
        children.setdefault(process.ppid, []).append(process.pid)

    ordered: list[int] = []

    def visit(pid: int) -> None:
        for child_pid in children.get(pid, []):
            visit(child_pid)
        ordered.append(pid)

    visit(root_pid)
    return ordered


def _orphaned_task_root(
    process: ProcessSnapshot,
    by_pid: dict[int, ProcessSnapshot],
) -> int:
    current = process
    task_root = process.pid
    seen = {process.pid}
    while current.ppid > 1 and current.ppid not in seen:
        seen.add(current.ppid)
        parent = by_pid.get(current.ppid)
        if parent is None:
            break
        if "multiprocessing.spawn" in parent.command:
            task_root = parent.pid
        current = parent
    return task_root


def _signal_existing(pid: int, sig: signal.Signals) -> bool:
    try:
        os.kill(pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def purge_orphaned_playwright_processes(*, dry_run: bool = False) -> PlaywrightCleanupResult:
    processes = _processes()
    drivers = find_orphaned_playwright_drivers(processes)
    by_pid = {process.pid: process for process in processes}
    orphaned_driver_ids = {driver.pid for driver in drivers}
    has_active_driver = any(
        "playwright/driver" in process.command.replace("\\", "/")
        and "run-driver" in process.command
        and process.pid not in orphaned_driver_ids
        for process in processes
    )
    detached_browsers = (
        [] if has_active_driver else find_detached_playwright_browsers(processes)
    )
    task_roots = {
        _orphaned_task_root(driver, by_pid)
        for driver in drivers
    } | {browser.pid for browser in detached_browsers}
    process_ids = list(
        dict.fromkeys(
            pid
            for root_pid in task_roots
            for pid in _tree_pids(root_pid, processes)
        )
    )
    if dry_run:
        return PlaywrightCleanupResult(
            drivers_found=len(drivers),
            processes_signalled=len(process_ids),
            browsers_found=len(detached_browsers),
        )

    signalled = sum(_signal_existing(pid, signal.SIGTERM) for pid in process_ids)
    if signalled:
        time.sleep(0.25)
        for pid in process_ids:
            _signal_existing(pid, signal.SIGKILL)
    return PlaywrightCleanupResult(
        drivers_found=len(drivers),
        processes_signalled=signalled,
        browsers_found=len(detached_browsers),
    )
