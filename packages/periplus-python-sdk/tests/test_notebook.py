"""Optional integration tests; release CI installs the notebook extra."""
import importlib.util
import unittest
from unittest.mock import patch
import httpx
from test_client import RESULT, stream_response


@unittest.skipUnless(importlib.util.find_spec('sqlalchemy') and importlib.util.find_spec('marimo'), 'notebook extra required')
class NotebookTests(unittest.TestCase):
    def engine(self, *, truncated=False):
        from periplus_sdk import sql_api
        factory = httpx.Client
        self.requests = []
        def handler(request):
            import json
            sql = json.loads(request.content)['sql']
            self.requests.append(sql)
            columns, types, rows = ['n'], ['INTEGER'], [[42]]
            if sql.startswith('SHOW TABLES'):
                columns, types, rows = ['name'], ['VARCHAR'], [['capture']]
            if sql.startswith('DESCRIBE'):
                columns = ['column_name','column_type','null','key','default','extra']
                types = ['VARCHAR']*6
                rows = [['capture_id','UUID','NO',None,None,None],['captured_at','TIMESTAMP WITH TIME ZONE','YES',None,None,None]]
            return stream_response(dict(RESULT,columns=columns,types=types,rows=rows,truncated=truncated))
        patcher = patch('periplus_sdk.client.httpx.Client', side_effect=lambda **kw: factory(**kw,transport=httpx.MockTransport(handler)))
        patcher.start()
        self.addCleanup(patcher.stop)
        engine = sql_api.create_engine('https://public.example')
        self.addCleanup(engine.dispose)
        return engine

    def test_reflection_and_repeated_queries(self):
        from sqlalchemy import inspect, text
        engine=self.engine()
        inspector=inspect(engine)
        self.assertEqual(inspector.get_schema_names(),['public_v1'])
        self.assertEqual(inspector.get_table_names(),[])
        self.assertEqual(inspector.get_view_names(),['capture'])
        self.assertTrue(inspector.has_table('capture'))
        columns=inspector.get_columns('capture')
        self.assertEqual(columns[0]['name'],'capture_id')
        self.assertEqual(str(columns[1]['type']),'TIMESTAMP WITH TIME ZONE')
        self.assertEqual(inspector.get_pk_constraint('capture')['constrained_columns'],[])
        for _ in range(2):
            with engine.connect() as c:
                self.assertEqual(c.execute(text('SELECT :n AS n'),{'n':42}).fetchall(),[(42,)])
        self.assertFalse(any('ROLLBACK' in s or 'BEGIN' in s for s in self.requests))

    def test_marimo_discovery_schema_browser_and_sql_cell(self):
        import marimo as mo
        from marimo._sql.get_engines import get_engines_from_variables
        engine=self.engine()
        found=get_engines_from_variables([('pp',engine)])
        self.assertEqual(len(found),1)
        adapter=found[0][1]
        databases=adapter.get_databases(include_schemas=True,include_tables=True,include_table_details=True)
        self.assertEqual(databases[0].name,'periplus')
        self.assertEqual(databases[0].schemas[0].name,'public_v1')
        table=databases[0].schemas[0].tables[0]
        self.assertEqual(table.name,'capture')
        self.assertEqual([c.name for c in table.columns],['capture_id','captured_at'])
        df=mo.sql('SELECT 42 AS n',engine=engine,output=False)
        self.assertEqual(df.rows(),[(42,)])

    def test_dbapi_discovery(self):
        from periplus_sdk import connect
        from marimo._sql.engines.dbapi import DBAPIEngine
        with connect('https://public.example') as c:
            self.assertTrue(DBAPIEngine.is_compatible(c))

    def test_truncated_discovery_is_not_silently_partial(self):
        from sqlalchemy import inspect, exc
        engine=self.engine(truncated=True)
        with self.assertRaises(exc.OperationalError):
            inspect(engine).get_view_names()

    def test_reflection_quotes_identifiers_and_rejects_private_schemas(self):
        from sqlalchemy import inspect, exc
        engine=self.engine()
        inspector=inspect(engine)
        inspector.get_columns('odd"name')
        self.assertIn('DESCRIBE "public_v1"."odd""name"',self.requests)
        with self.assertRaises(exc.InvalidRequestError):
            inspector.get_columns('visits',schema='ingest')

    def test_python_filtered_ids_bind_into_native_marimo_sql(self):
        import json
        import marimo as mo
        from periplus_sdk import sql_api
        from sqlalchemy import inspect
        from marimo._sql.get_engines import get_engines_from_variables
        factory = httpx.Client
        engine = self.engine()
        captured = []
        def handler(request):
            payload = json.loads(request.content)
            captured.append(payload)
            if payload['sql'].startswith('SHOW TABLES'):
                self.assertEqual(payload['parameters'], [])
                return stream_response(dict(RESULT, columns=['name'], types=['VARCHAR'], rows=[['capture']], truncated=False))
            ids = payload['parameters'][0]
            return stream_response(dict(RESULT, columns=['content_id'], types=['VARCHAR'],
                                        rows=[[value] for value in ids], truncated=False))
        with engine.connect() as connection:
            client = connection.connection.dbapi_connection._client
            client._http.close()
            client._http = factory(base_url='https://public.example', transport=httpx.MockTransport(handler))
        # A Python-filtered selection larger than the previous entire request budget.
        ids = [f'{i:064x}' for i in range(5000) if i % 2 == 0]
        selected = sql_api.bind(engine, content_ids=ids)
        self.assertEqual(len(get_engines_from_variables([('selected', selected)])), 1)
        frame = mo.sql('SELECT unnest(CAST(:content_ids AS VARCHAR[])) AS content_id', engine=selected, output=False)
        self.assertEqual(frame['content_id'].to_list(), ids)
        self.assertEqual(captured[0]['parameters'], [ids])
        self.assertLess(len(captured[0]['sql']), 100)
        self.assertEqual(inspect(selected).get_view_names(), ['capture'])
        # Empty selections remain typed SQL lists, rather than invalid IN ().
        empty = sql_api.bind(engine, content_ids=[])
        frame = mo.sql('SELECT unnest(CAST(:content_ids AS VARCHAR[])) AS content_id', engine=empty, output=False)
        self.assertEqual(frame.height, 0)
