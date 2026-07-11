from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from runtime.progress import ProgressPublisher, _connect_nats, stream_progress
from actions.shared.progress import ProgressEvent
from api.routers.task_runs import _progress_stream


class ProgressPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_best_effort_connections_disable_reconnect_noise(self) -> None:
        with patch("runtime.progress.nats.connect", AsyncMock(return_value="client")) as connect:
            client = await _connect_nats()

        self.assertEqual(client, "client")
        self.assertFalse(connect.call_args.kwargs["allow_reconnect"])
        self.assertEqual(connect.call_args.kwargs["max_reconnect_attempts"], 1)
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 1)
        self.assertTrue(callable(connect.call_args.kwargs["error_cb"]))

    async def test_publish_failure_is_best_effort(self) -> None:
        publisher = ProgressPublisher(uuid4(), attempt=2)
        publisher._jetstream = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("offline")))

        await publisher.publish("progress", {"value": 1})

        self.assertIsNone(publisher._jetstream)

    async def test_progress_payload_omits_empty_optional_fields(self) -> None:
        publisher = ProgressPublisher(uuid4(), attempt=2)
        publisher._jetstream = SimpleNamespace(publish=AsyncMock())

        await publisher.progress(
            ProgressEvent(
                operation_id="operation-1",
                phase="task",
                status="started",
                message="Task started.",
            )
        )

        payload = json.loads(publisher._jetstream.publish.await_args.args[1])
        self.assertEqual(payload["event_id"], "2:1")
        self.assertEqual(
            payload["data"],
            {
                "phase": "task",
                "status": "started",
                "message": "Task started.",
                "operation_id": "operation-1",
            },
        )

    async def test_skipped_event_closes_progress_subscription(self) -> None:
        run_id = uuid4()
        message = SimpleNamespace(
            data=json.dumps(
                {
                    "event_id": "2:1",
                    "run_id": str(run_id),
                    "attempt": 2,
                    "sequence": 1,
                    "type": "skipped",
                    "timestamp": "2026-07-10T00:00:00+00:00",
                    "data": {"status": "skipped", "error": None},
                }
            ).encode(),
            ack=AsyncMock(),
        )
        subscription = SimpleNamespace(
            next_msg=AsyncMock(return_value=message),
            unsubscribe=AsyncMock(),
        )
        client = SimpleNamespace(drain=AsyncMock())
        jetstream = SimpleNamespace(subscribe=AsyncMock(return_value=subscription))

        with patch("runtime.progress._jetstream", AsyncMock(return_value=(client, jetstream))):
            events = [event async for event in stream_progress(run_id, current_attempt=2)]

        self.assertEqual([event["type"] for event in events], ["skipped"])
        subscription.next_msg.assert_awaited_once()

    async def test_retry_skips_retained_events_from_older_attempts(self) -> None:
        run_id = uuid4()

        def message(attempt: int, sequence: int, event_type: str):
            return SimpleNamespace(
                data=json.dumps(
                    {
                        "event_id": f"{attempt}:{sequence}",
                        "run_id": str(run_id),
                        "attempt": attempt,
                        "sequence": sequence,
                        "type": event_type,
                        "timestamp": "2026-07-10T00:00:00+00:00",
                        "data": {},
                    }
                ).encode(),
                ack=AsyncMock(),
            )

        subscription = SimpleNamespace(
            next_msg=AsyncMock(
                side_effect=[
                    message(1, 8, "failed"),
                    message(2, 1, "progress"),
                    message(2, 2, "succeeded"),
                ]
            ),
            unsubscribe=AsyncMock(),
        )
        client = SimpleNamespace(drain=AsyncMock())
        jetstream = SimpleNamespace(subscribe=AsyncMock(return_value=subscription))

        with patch("runtime.progress._jetstream", AsyncMock(return_value=(client, jetstream))):
            events = [event async for event in stream_progress(run_id, current_attempt=2)]

        self.assertEqual([event["event_id"] for event in events], ["2:1", "2:2"])


class TaskRunProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_already_finished_run_uses_progress_envelope(self) -> None:
        run_id = uuid4()
        run = SimpleNamespace(id=run_id, status="failed", error="boom", attempt=2)

        with patch("api.routers.task_runs._read_run", return_value=run):
            event = await anext(_progress_stream(run_id, after_event_id=(2, 7)))

        data_line = next(line for line in event.splitlines() if line.startswith("data: "))
        envelope = json.loads(data_line.removeprefix("data: "))
        self.assertEqual(envelope["run_id"], str(run_id))
        self.assertEqual(envelope["sequence"], 8)
        self.assertEqual(envelope["attempt"], 2)
        self.assertEqual(envelope["event_id"], "2:8")
        self.assertEqual(envelope["type"], "failed")
        self.assertEqual(envelope["data"], {"status": "failed", "error": "boom"})

    async def test_missing_terminal_message_falls_back_to_postgres(self) -> None:
        run_id = uuid4()
        running = SimpleNamespace(id=run_id, status="running", error=None, attempt=3)
        succeeded = SimpleNamespace(id=run_id, status="succeeded", error=None, attempt=3)

        async def unavailable(*_args, **_kwargs):
            if False:
                yield {}
            raise RuntimeError("offline")

        with (
            patch("api.routers.task_runs._read_run", side_effect=[running, succeeded]),
            patch("api.routers.task_runs.stream_progress", unavailable),
        ):
            stream = _progress_stream(run_id, after_event_id=(2, 9))
            started_event = await anext(stream)
            event = await anext(stream)

        self.assertIn('"phase": "task"', started_event)
        self.assertIn("event: succeeded", event)
