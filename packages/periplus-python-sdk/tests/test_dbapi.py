import unittest
from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import patch

import httpx

from periplus_sdk import connect, dbapi
from test_client import RESULT, stream_response


def response(**kw):
    return dict(RESULT, truncated=False, **kw)


class DBAPITests(unittest.TestCase):
    def connection(self, handler=None, **options):
        factory = httpx.Client
        handler = handler or (lambda request: httpx.Response(200, json=response()))
        def streaming_handler(request):
            result = handler(request)
            return stream_response(result.json()) if result.is_success else result
        with patch('periplus_sdk.client.httpx.Client' , side_effect=lambda **kw:
                   factory(**kw, transport=httpx.MockTransport(streaming_handler))):
            c = connect('https://public.example/prefix', **options)
        self.addCleanup(c.close)
        return c

    def test_cursor_consumption_description_and_metadata(self):
        c = self.connection()
        cur = c.cursor()
        with self.assertRaises(dbapi.ProgrammingError):
            cur.fetchone()
        cur.execute('SELECT ? AS n', [1])
        self.assertEqual(cur.rowcount, -1)
        self.assertEqual([d[0] for d in cur.description], ['n', 'n'])
        self.assertEqual(cur.description[1][1], 'DECIMAL(20,2)')
        self.assertEqual(cur.fetchmany(0), [])
        self.assertEqual(cur.fetchone(), (9007199254740993, Decimal('123.45')))
        self.assertIsNone(cur.fetchone())
        self.assertEqual(cur.fetchall(), [])
        self.assertEqual(cur.result.source_snapshot, 4)
        self.assertIs(c.last_result, cur.result)
        cur.execute('SELECT 1')
        self.assertEqual(list(cur), [(9007199254740993, Decimal('123.45'))])
        other = c.execute('SELECT 1')
        self.assertEqual(other.rowcount, -1)
        self.assertEqual(cur.fetchall(), [])

    def test_empty_and_fetchmany(self):
        c = self.connection(lambda r: httpx.Response(200, json=response(columns=['n'], types=['INTEGER'], rows=[[1],[2],[3]])))
        cur = c.execute('SELECT 1')
        cur.arraysize = 2
        self.assertEqual(cur.fetchmany(), [(1,), (2,)])
        self.assertEqual(cur.fetchmany(10), [(3,)])
        with self.assertRaises(dbapi.ProgrammingError):
            cur.fetchmany(-1)
        c = self.connection(lambda r: httpx.Response(200, json=response(rows=[])))
        cur = c.execute('SELECT 1 WHERE false')
        self.assertEqual(cur.rowcount, -1)
        self.assertEqual(len(cur.description), 2)
        self.assertEqual(cur.fetchall(), [])

    def test_modes_parameters_and_scalar_decoding(self):
        requests = []
        def handler(r):
            requests.append(r)
            return httpx.Response(200, json=response(columns=['d','t','ts','b','f','n','s'],
                types=['DATE','TIME','TIMESTAMP WITH TIME ZONE','BLOB','DOUBLE','BIGINT','VARCHAR'],
                rows=[['2026-09-11','12:34:56','2026-09-11T12:34:56+00:00','aGk=','inf',None,'9007199254740993']]))
        c = self.connection(handler, mode='experimental')
        row = c.execute('SELECT CAST(? AS DATE)', [date(2026,9,11)]).fetchone()
        self.assertEqual(row, (date(2026,9,11),time(12,34,56),datetime.fromisoformat('2026-09-11T12:34:56+00:00'),b'hi',float('inf'),None,'9007199254740993'))
        self.assertEqual(requests[0].url.path, '/prefix/api/query/experimental/exec')
        self.assertEqual(requests[0].headers['x-periplus-query-source'], 'sdk')

    def test_truncation_and_lifecycle(self):
        c = self.connection(lambda r: httpx.Response(200,json=RESULT))
        cur = c.execute('SELECT 1')
        with self.assertRaises(dbapi.OperationalError):
            cur.fetchall()
        self.assertEqual(cur.rowcount, -1)
        self.assertTrue(c.last_result.truncated)
        c.commit()
        with self.assertRaises(dbapi.NotSupportedError):
            c.rollback()
        with self.assertRaises(dbapi.NotSupportedError):
            cur.executemany('SELECT 1', [])
        cur.close()
        with self.assertRaises(dbapi.InterfaceError):
            cur.fetchall()
        c.close()
        c.close()
        with self.assertRaises(dbapi.InterfaceError):
            c.cursor()

    def test_failures_reset_results_and_preserve_errors(self):
        calls = []
        def handler(r):
            calls.append(r)
            return httpx.Response(200,json=response()) if len(calls)==1 else httpx.Response(429,json={'code':'service_busy','detail':'Busy'},headers={'Retry-After':'2'})
        c = self.connection(handler)
        cur = c.execute('SELECT 1')
        with self.assertRaises(dbapi.OperationalError) as e:
            cur.execute('SELECT 1')
        self.assertEqual(e.exception.status_code,429)
        self.assertEqual(e.exception.retry_after_seconds,2)
        self.assertEqual(e.exception.code,'service_busy')
        self.assertIsNone(c.last_result)
        self.assertIsNone(cur.description)
        self.assertEqual(len(calls),2)
        with self.assertRaises(dbapi.ProgrammingError):
            cur.fetchall()
        for params in ({'n':1},'bad',[object()]):
            with self.assertRaises(dbapi.ProgrammingError):
                cur.execute('SELECT ?',params)
        self.assertEqual(len(calls),2)

    def test_malformed_rows(self):
        c=self.connection(lambda r:httpx.Response(200,json=response(rows=[[1]])))
        with self.assertRaises(dbapi.InterfaceError):
            c.execute('SELECT 1').fetchall()
