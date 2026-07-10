from __future__ import annotations

import threading
import time

from .recorder import record, snapshot

_WAITER_COUNTS: dict[tuple[str, str], int] = {}
_LOCK = threading.Lock()


def _change_waiters(scope: str, policy: str, delta: int) -> None:
    key = (scope, policy)
    with _LOCK:
        count = max(0, _WAITER_COUNTS.get(key, 0) + delta)
        if count:
            _WAITER_COUNTS[key] = count
        else:
            _WAITER_COUNTS.pop(key, None)
    snapshot("atlas_crawl_permit_waiters", count, scope=scope, policy=policy)


def _refresh_waiters(scope: str, policy: str) -> None:
    with _LOCK:
        count = _WAITER_COUNTS.get((scope, policy), 0)
    snapshot("atlas_crawl_permit_waiters", count, scope=scope, policy=policy)


class CapacityWaitMetrics:
    def __init__(self, *, policy: str) -> None:
        self._policy = policy
        self._started_at = time.perf_counter()
        self._blocked_scope = "none"
        self._waiter_scope: str | None = None
        self._finished = False
        self._last_snapshot_at = self._started_at

    def blocked_by(self, scope: str) -> None:
        if scope not in {"browser", "policy", "unknown"}:
            scope = "unknown"
        self._blocked_scope = scope
        if self._waiter_scope == scope:
            now = time.perf_counter()
            if now - self._last_snapshot_at >= 5:
                _refresh_waiters(scope, self._policy)
                self._last_snapshot_at = now
            return
        if self._waiter_scope is not None:
            _change_waiters(self._waiter_scope, self._policy, -1)
        self._waiter_scope = scope
        _change_waiters(scope, self._policy, 1)
        self._last_snapshot_at = time.perf_counter()

    def finish(self, outcome: str) -> None:
        if self._finished:
            return
        self._finished = True
        if self._waiter_scope is not None:
            _change_waiters(self._waiter_scope, self._policy, -1)
        record(
            "atlas_crawl_permit_wait_duration_seconds",
            time.perf_counter() - self._started_at,
            blocked_scope=self._blocked_scope,
            policy=self._policy,
            outcome=outcome,
        )
        if outcome == "timeout":
            record(
                "atlas_crawl_permit_timeouts_total",
                blocked_scope=self._blocked_scope,
                policy=self._policy,
            )


def lease_lost(*, scope: str, policy: str) -> None:
    record("atlas_crawl_permit_lease_losses_total", scope=scope, policy=policy)
