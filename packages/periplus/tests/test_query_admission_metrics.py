"""Query occupancy excludes rejected work and releases on every terminal path."""
import unittest
from unittest.mock import Mock, patch

from periplus.platform.clickhouse import ClickHouseConfig
from periplus.query.models import QueryRequest
from periplus.query.service import BusyError, QueryService, _active_queries


class QueryAdmissionMetricTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        with patch('periplus.query.service.ClickHouseClient', return_value=self.client):
            self.service = QueryService(ClickHouseConfig(url='http://localhost:8123',
                username='reader', password='test', query_only=True))
        self.addCleanup(self.service.close)
        self.addCleanup(lambda: self.assertEqual(_active_queries._value.get(), 0))
        self.client.reset_mock()

    def test_success_and_failure_release_occupancy(self):
        for failure in (False, True):
            def execute(sql, **kwargs):
                self.assertEqual(_active_queries._value.get(), 1)
                if failure:
                    raise OSError('failed operation')
                if sql.startswith('EXPLAIN'):
                    return b'Expression'
                return b'{"meta":[],"data":[[]]}'
            self.client.execute.side_effect = execute
            if failure:
                with self.assertRaises(OSError):
                    self.service.execute(QueryRequest(sql='SELECT 1'))
            else:
                self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 1')).row_count, 1)
            self.assertEqual(_active_queries._value.get(), 0)
            self.assertFalse(self.service._lock.locked())

    def test_busy_rejection_does_not_increment_occupancy_or_contact_database(self):
        with self.service._lock, self.assertRaises(BusyError):
            self.service.execute(QueryRequest(sql='SELECT 1'))
        self.assertEqual(_active_queries._value.get(), 0)
        self.client.execute.assert_not_called()

    def test_validation_failure_releases_occupancy_without_database_io(self):
        with self.assertRaises(ValueError):
            self.service.execute(QueryRequest(sql='DROP TABLE ingest.visits'))
        self.assertEqual(_active_queries._value.get(), 0)
        self.assertFalse(self.service._lock.locked())
        self.client.execute.assert_not_called()
