from __future__ import annotations

import unittest

from actions.shared.progress import ProgressEvent, ProgressReporter


class ProgressReporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_check_is_explicit(self) -> None:
        calls: list[str] = []

        async def check() -> None:
            calls.append("check")

        async def sink(_event: ProgressEvent) -> None:
            calls.append("sink")

        reporter = ProgressReporter(sink=sink, cancellation_check=check)
        await reporter.check_cancelled()
        await reporter.emit(ProgressEvent(phase="schema", status="started"))

        self.assertEqual(calls, ["check", "sink"])

    async def test_phase_emits_paired_lifecycle_events(self) -> None:
        events: list[ProgressEvent] = []
        reporter = ProgressReporter(sink=events.append)

        async with reporter.phase("schema", resource="https://example.com"):
            pass

        self.assertEqual([event.status for event in events], ["started", "succeeded"])
        self.assertEqual(events[0].operation_id, events[1].operation_id)
        self.assertGreaterEqual(events[-1].duration or 0, 0)

    async def test_phase_reports_failure_and_reraises(self) -> None:
        events: list[ProgressEvent] = []
        reporter = ProgressReporter(sink=events.append)

        with self.assertRaisesRegex(RuntimeError, "boom"):
            async with reporter.phase("schema"):
                raise RuntimeError("boom")

        self.assertEqual([event.status for event in events], ["started", "failed"])
        self.assertEqual(events[-1].error, "boom")
