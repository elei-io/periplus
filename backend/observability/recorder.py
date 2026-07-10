from __future__ import annotations

import logging
import queue
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Callable
from typing import Iterator, Protocol

from .catalog import definition_for, validate_labels

_LOG = logging.getLogger("atlas.observability")


@dataclass(frozen=True)
class MetricObservation:
    name: str
    value: float
    labels: dict[str, str]
    operation: str = "record"
    child_id: str | None = None


class MetricRecorder(Protocol):
    def record(self, name: str, value: float = 1.0, **labels: str) -> None: ...
    def snapshot(self, name: str, value: float, **labels: str) -> None: ...


class NoopRecorder:
    def record(self, name: str, value: float = 1.0, **labels: str) -> None:
        return None

    def snapshot(self, name: str, value: float, **labels: str) -> None:
        return None


class InMemoryRecorder:
    def __init__(self) -> None:
        self.observations: list[MetricObservation] = []

    def _append(self, name: str, value: float, operation: str, labels: dict[str, str]) -> None:
        definition = definition_for(name)
        normalized = validate_labels(definition, labels)
        self.observations.append(MetricObservation(name, float(value), normalized, operation))

    def record(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._append(name, value, "record", labels)

    def snapshot(self, name: str, value: float, **labels: str) -> None:
        self._append(name, value, "snapshot", labels)

    def matching(self, name: str, **labels: str) -> list[MetricObservation]:
        return [
            observation
            for observation in self.observations
            if observation.name == name
            and all(observation.labels.get(key) == value for key, value in labels.items())
        ]

    def total(self, name: str, **labels: str) -> float:
        return sum(observation.value for observation in self.matching(name, **labels))


class QueueRecorder:
    def __init__(self, output_queue, *, child_id: str) -> None:
        self._queue = output_queue
        self._child_id = child_id
        self._drop_logged = False

    def _send(self, name: str, value: float, operation: str, labels: dict[str, str]) -> None:
        try:
            definition = definition_for(name)
            normalized = validate_labels(definition, labels)
            observation = MetricObservation(name, float(value), normalized, operation, self._child_id)
            self._queue.put_nowait(observation)
        except (queue.Full, BrokenPipeError, EOFError, OSError, TypeError, ValueError):
            if not self._drop_logged:
                self._drop_logged = True
                _LOG.warning("metric observation dropped", extra={"metric": name})

    def record(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._send(name, value, "record", labels)

    def snapshot(self, name: str, value: float, **labels: str) -> None:
        self._send(name, value, "snapshot", labels)


class CallbackRecorder:
    def __init__(self, callback: Callable[[MetricObservation], None], *, child_id: str | None = None) -> None:
        self._callback = callback
        self._child_id = child_id

    def _apply(self, name: str, value: float, operation: str, labels: dict[str, str]) -> None:
        try:
            definition = definition_for(name)
            normalized = validate_labels(definition, labels)
            self._callback(MetricObservation(name, float(value), normalized, operation, self._child_id))
        except Exception:
            _LOG.warning("metric observation dropped", extra={"metric": name})

    def record(self, name: str, value: float = 1.0, **labels: str) -> None:
        self._apply(name, value, "record", labels)

    def snapshot(self, name: str, value: float, **labels: str) -> None:
        self._apply(name, value, "snapshot", labels)


_RECORDER: ContextVar[MetricRecorder] = ContextVar("atlas_metric_recorder", default=NoopRecorder())


def current_recorder() -> MetricRecorder:
    return _RECORDER.get()


@contextmanager
def metric_recorder_scope(recorder: MetricRecorder) -> Iterator[None]:
    token = _RECORDER.set(recorder)
    try:
        yield
    finally:
        _RECORDER.reset(token)


def record(name: str, value: float = 1.0, **labels: str) -> None:
    current_recorder().record(name, value, **labels)


def snapshot(name: str, value: float, **labels: str) -> None:
    current_recorder().snapshot(name, value, **labels)
