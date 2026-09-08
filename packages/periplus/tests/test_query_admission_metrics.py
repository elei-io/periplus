"""Query occupancy excludes rejected work and never leaks a busy slot."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from periplus.query.service import BusyError, QueryRequest, QueryService, _active_queries


class QueryAdmissionMetricTests(unittest.TestCase):
    def setUp(self):
        with patch.object(QueryService, '_connect', return_value=object()):
            self.service = QueryService(SimpleNamespace(alias='periplus'))
        self.addCleanup(lambda: self.assertEqual(_active_queries._value.get(), 0))

    def test_success_and_failure_release_occupancy(self):
        for failure in (False, True):
            def execute(*args, **kwargs):
                self.assertEqual(_active_queries._value.get(), 1)
                if failure:
                    raise ValueError('failed operation')
                return 'completed'
            with patch.object(self.service, '_run_admitted', side_effect=execute):
                if failure:
                    with self.assertRaises(ValueError):
                        self.service.execute(QueryRequest(sql='SELECT 1'))
                else:
                    self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 1')), 'completed')
            self.assertEqual(_active_queries._value.get(), 0)
            self.assertFalse(self.service._lock.locked())

    def test_busy_rejections_do_not_increment_occupancy(self):
        with self.service._lock, self.assertRaises(BusyError):
            self.service.execute(QueryRequest(sql='SELECT 1'))
        self.assertEqual(_active_queries._value.get(), 0)

    def test_connection_recovery_failure_releases_occupancy(self):
        self.service.connection = None
        with patch.object(self.service, '_connect', side_effect=OSError('storage unavailable')), self.assertRaises(OSError):
            self.service.execute(QueryRequest(sql='SELECT 1'))
        self.assertEqual(_active_queries._value.get(), 0)
        self.assertFalse(self.service._lock.locked())
