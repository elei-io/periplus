from datetime import UTC, datetime
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from periplus_sdk import frontier
from periplus_sdk.types import AcquisitionView, CurrentActivity, LiveView


class FrontierReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_preserves_unavailable_history_and_current_counts(self):
        now = datetime.now(UTC)
        value = LiveView(workers=dict(as_of=now, state="no_recent_reports"), current=CurrentActivity(as_of=now, paused=False, queued=2, dispatched=1,
            started=0, oldest_wait_at=now, domains=[], more_domains=False, upcoming=[], recent=[]),
            history=None, history_unavailable_reason='catalogue_history_unavailable', recent=[])
        with patch('periplus_sdk.frontier.request', AsyncMock(return_value=value.model_dump(mode='json'))) as request:
            result = await frontier.live()
        self.assertIsNone(result.history)
        self.assertEqual(result.current.queued, 2)
        self.assertIsNone(result.current.next_start_estimate)
        request.assert_awaited_once_with('GET', '/frontier/live')

    async def test_live_preserves_all_three_readiness_states(self):
        now = datetime.now(UTC)
        for ready, reason in ((True, 'active_generation_committed'), (False, 'materialization_pending'),
                              (None, 'active_generation_unavailable')):
            value = LiveView(workers=dict(as_of=now, state="no_recent_reports"), current=CurrentActivity(as_of=now, paused=False, queued=0, dispatched=0,
                started=0, oldest_wait_at=None, domains=[], more_domains=False, upcoming=[], recent=[]),
                history=None, history_unavailable_reason='catalogue_history_unavailable', recent=[dict(
                    observation_id=uuid4(), requested_url='https://example.com/', completed_at=now,
                    evidence_committed=True, query_ready=ready, query_readiness_reason=reason)])
            with patch('periplus_sdk.frontier.request', AsyncMock(return_value=value.model_dump(mode='json'))):
                result = await frontier.live()
            self.assertIs(result.recent[0].query_ready, ready)
            self.assertEqual(result.recent[0].query_readiness_reason, reason)

    async def test_item_identity_cannot_change_and_paths_require_uuid(self):
        identity, now = uuid4(), datetime.now(UTC)
        item = AcquisitionView(id=identity, url='https://example.com/', domain='example.com', status='queued',
            created_at=now, completed_at=None, attempt_count=0, observation_id=None, evidence_committed=False,
            eligibility_not_before=now, waiting_reason='awaiting_scheduler_evaluation',
            estimate_unavailable_reason='domain_permits_and_dispatch_capacity_not_observed',
            callers=[], more_callers=False, background=False, background_parent_observation_id=None,
            background_rule_id=None, as_of=now)
        with patch('periplus_sdk.frontier.request', AsyncMock(return_value=item.model_dump(mode='json'))) as request:
            result = await frontier.item(str(identity))
            self.assertIsNone(result.query_ready)
            request.assert_awaited_once_with('GET', f'/frontier/items/{identity}')
            for ready in (False, True):
                request.return_value = item.model_copy(update={'query_ready': ready}).model_dump(mode='json')
                self.assertIs((await frontier.item(identity)).query_ready, ready)
            with self.assertRaisesRegex(ValueError, 'identity'):
                await frontier.item(uuid4())
        with patch('periplus_sdk.frontier.request', AsyncMock()) as request:
            with self.assertRaises(ValueError):
                await frontier.item('../controls')
            request.assert_not_awaited()
