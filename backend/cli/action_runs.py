from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import TypeAdapter

from actions.shared.progress import ProgressEvent
from cli.config import api_url
from tasks.schemas import TaskPrimitive


def _check(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise RuntimeError(str(detail or response.reason_phrase))


def run_action(
    primitive: TaskPrimitive,
    input_value: dict[str, Any],
    response_adapter: TypeAdapter,
    progress_consumer: Callable[[ProgressEvent], None] | None = None,
) -> Any:
    base_url = api_url()
    with httpx.Client(base_url=base_url, timeout=None) as client:
        submission_response = client.post(f"/{primitive}/", json=input_value)
        _check(submission_response)
        run_id = submission_response.json()["run_id"]

        terminal_error: str | None = None
        with client.stream("GET", f"/task-runs/{run_id}/progress", headers={"Accept": "text/event-stream"}) as response:
            _check(response)
            event_type = "message"
            data_lines: list[str] = []
            for line in response.iter_lines():
                if not line:
                    if data_lines:
                        payload = json.loads("\n".join(data_lines))
                        if event_type == "progress" and progress_consumer is not None:
                            event = ProgressEvent(**payload["data"])
                            progress_consumer(event)
                        elif event_type in {"failed", "cancelled", "skipped", "error"}:
                            data = payload.get("data", payload)
                            terminal_error = data.get("error") or data.get("message") or event_type
                    event_type = "message"
                    data_lines = []
                    continue
                if line.startswith("event:"):
                    event_type = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").lstrip())

        if terminal_error:
            raise RuntimeError(terminal_error)
        while True:
            status_response = client.get(f"/task-runs/{run_id}")
            _check(status_response)
            run = status_response.json()
            if run["status"] in {"succeeded", "failed", "cancelled", "skipped"}:
                if run["status"] != "succeeded":
                    raise RuntimeError(run.get("error") or f"Task run {run['status']}.")
                break
            time.sleep(1)
        result_response = client.get(f"/task-runs/{run_id}/result")
        _check(result_response)
        return response_adapter.validate_python(result_response.json())
