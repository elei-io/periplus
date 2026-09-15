import unittest
import sqlglot
from sqlglot import exp
from periplus.query.models import QueryRequest
from periplus.query.service import public_sql


class ClickHouseQueryValidationTests(unittest.TestCase):
    def test_cte_parameters_follow_source_order(self):
        result = public_sql(QueryRequest(sql='WITH a AS (SELECT ? AS x) SELECT ? FROM a WHERE x=?',
                                       parameters=[1, 2, 3]))
        self.assertIn('SELECT 1 AS x', result)
        self.assertIn('SELECT 2 FROM a WHERE x = 3', result)

    def test_parameter_cannot_become_executable_sql(self):
        value = "'); DROP VIEW public_v1.page; SELECT ('"
        result = public_sql(QueryRequest(sql='SELECT ? AS value', parameters=[value]))
        statements = sqlglot.parse(result, read='clickhouse')
        self.assertEqual(len(statements), 1)
        self.assertEqual(next(statements[0].find_all(exp.Literal)).this, value)

    def test_quoted_question_mark_is_not_a_parameter(self):
        self.assertIn("'?'", public_sql(QueryRequest(sql="SELECT '?'")))

    def test_private_sources_mutations_and_table_functions_are_rejected(self):
        for sql in ('SELECT * FROM ingest.visits', 'SELECT * FROM system.query_log',
                    "SELECT * FROM url('https://example.com/')", 'DROP VIEW public_v1.page',
                    'SELECT 1; SELECT 2'):
            with self.subTest(sql=sql), self.assertRaises(ValueError):
                public_sql(QueryRequest(sql=sql))

    def test_wire_integers_preserve_precision_recursively(self):
        from periplus.query.service import wire_value
        value = [2**53-1, 2**53, -(2**53), True, None, {'items': [2**64-1]}]
        self.assertEqual(wire_value(value), [2**53-1, str(2**53), str(-(2**53)),
                         True, None, {'items': [str(2**64-1)]}])

    def test_unqualified_public_relation_is_qualified(self):
        self.assertEqual(public_sql(QueryRequest(sql='SELECT text FROM html_element')), 'SELECT text FROM public_v1.html_element')



class ClickHouseQueryExecutionTests(unittest.TestCase):
    def setUp(self):
        import json
        from unittest.mock import Mock, patch
        from periplus.platform.clickhouse import ClickHouseConfig
        from periplus.query.service import QueryService
        self.rows = [[1], [2]]
        self.client = Mock()

        def execute(sql, **kwargs):
            if sql.startswith('EXPLAIN PLAN '):
                return b'Expression\n'
            if 'FORMAT JSONCompact' in sql:
                return json.dumps({'meta': [{'name': 'n', 'type': 'UInt64'}], 'data': self.rows}).encode()
            return b''

        self.client.execute.side_effect = execute
        factory = patch('periplus.query.service.ClickHouseClient', return_value=self.client)
        factory.start()
        self.addCleanup(factory.stop)
        self.service = QueryService(ClickHouseConfig(url='http://localhost:8123',
            username='reader', password='test', query_only=True))
        self.addCleanup(self.service.close)
        self.client.reset_mock()

    def test_preparation_and_stream_preserve_parameters_rows_and_no_snapshot_claim(self):
        from periplus.query.models import QueryRequest
        payload = QueryRequest(sql='SELECT ? AS n', parameters=[1])
        prepared = self.service.prepare(payload)
        self.assertEqual(prepared.parameters, [1])
        self.assertEqual(self.client.execute.call_count, 1)
        frames = []
        result = self.service.execute(payload, emit=frames.append)
        self.assertEqual(result.rows, self.rows)
        self.assertIsNone(result.source_snapshot)
        self.assertEqual([frame['type'] for frame in frames], ['metadata', 'rows'])
        self.assertEqual(frames[1]['rows'], result.rows)
        self.assertIsNone(frames[0]['source_snapshot'])

    def test_row_and_byte_budgets_are_independent(self):
        from periplus.operations.access.schemas import QueryLimits
        from periplus.query.models import QueryRequest
        payload = QueryRequest(sql='SELECT 1 AS n')
        result = self.service.execute(payload, limits=QueryLimits(max_rows=1))
        self.assertEqual(result.rows, [[1]])
        self.assertEqual(result.truncation_reason, 'max_rows')
        self.rows = [['x'*(2*1024*1024)]]
        result = self.service.execute(payload, limits=QueryLimits(max_result_bytes=1024*1024))
        self.assertEqual(result.rows, [])
        self.assertEqual(result.truncation_reason, 'max_result_bytes')
        self.assertLessEqual(result.result_bytes, 1024*1024)

    def test_busy_rejection_and_failed_delivery_release_admission(self):
        from periplus.query.models import QueryRequest
        from periplus.query.service import BusyError
        payload = QueryRequest(sql='SELECT 1')
        self.service._lock.acquire()
        try:
            with self.assertRaises(BusyError):
                self.service.execute(payload)
        finally:
            self.service._lock.release()
        self.client.execute.assert_not_called()

        def disconnect(frame):
            raise ConnectionError('consumer disconnected')

        with self.assertRaises(ConnectionError):
            self.service.execute(payload, emit=disconnect)
        self.assertEqual(self.service.execute(payload).rows, self.rows)

    def test_database_failure_is_not_replayed_and_service_can_be_reused(self):
        from periplus.query.models import QueryRequest
        original = self.client.execute.side_effect
        self.client.execute.side_effect = OSError('transport unavailable')
        with self.assertRaises(OSError):
            self.service.execute(QueryRequest(sql='SELECT 1'))
        self.assertEqual(self.client.execute.call_count, 1)
        self.client.execute.side_effect = original
        self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 1')).rows, self.rows)

    def test_closed_service_rejects_prepare_and_execute_without_transport(self):
        from periplus.query.service import _active_queries
        self.service.close()
        self.assertFalse(self.service.healthy)
        for operation in (self.service.prepare, self.service.execute):
            with self.assertRaisesRegex(RuntimeError, 'closed'):
                operation(QueryRequest(sql='SELECT 1'))
            self.assertFalse(self.service._lock.locked())
            self.assertEqual(_active_queries._value.get(), 0)
        self.client.execute.assert_not_called()

    def test_already_cancelled_request_never_contacts_database(self):
        import threading
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(TimeoutError):
            self.service.execute(QueryRequest(sql='SELECT 1'), cancelled=cancelled)
        self.client.execute.assert_not_called()
        self.assertEqual(self.service.execute(QueryRequest(sql='SELECT 1')).rows, self.rows)


if __name__ == '__main__':
    unittest.main()
