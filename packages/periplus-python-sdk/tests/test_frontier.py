from datetime import UTC, datetime
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from periplus_sdk import frontier
from periplus_sdk.errors import ApiError
from periplus_sdk.types import ObservationLineagePage


class FrontierLineageTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_preserves_identity_cursor_and_partial_evidence_semantics(self):
        identity = uuid4()
        page = ObservationLineagePage(observation_id=identity, requested_url='https://example.com/',
            items=[], next_cursor='next-page', as_of=datetime.now(UTC))
        with patch('periplus_sdk.frontier.request', AsyncMock(return_value=page.model_dump(mode='json'))) as request:
            result = await frontier.lineage(str(identity), limit=3, cursor='opaque')
            request.assert_awaited_once_with('GET', f'/frontier/observations/{identity}/lineage',
                params={'limit': 3, 'cursor': 'opaque'})
            self.assertEqual(result, page)
            with self.assertRaisesRegex(ValueError, 'identity'):
                await frontier.lineage(uuid4())

    async def test_invalid_bounds_do_not_read_and_unavailable_does_not_become_empty(self):
        with patch('periplus_sdk.frontier.request', AsyncMock()) as request:
            for call in (frontier.lineage('invalid'), frontier.lineage(uuid4(), limit=0),
                         frontier.lineage(uuid4(), cursor='x' * 513)):
                with self.assertRaises(ValueError):
                    await call
            request.assert_not_awaited()
        with patch('periplus_sdk.frontier.request', AsyncMock(side_effect=ApiError('unavailable', status_code=503))):
            with self.assertRaises(ApiError):
                await frontier.lineage(uuid4())
