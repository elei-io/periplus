"""Read-only same-snapshot experiment using the shared query bench."""
from dataclasses import asdict, replace
from pathlib import Path
from time import perf_counter
import json
import os
from periplus.query.benchmarking import QueryCase, _connection, _measure, deadline

sql = """SELECT h.level,h.text,c.effective_url AS url
FROM public_v1.html_heading h JOIN public_v1.capture c USING(content_id)
WHERE requested_url ILIKE '%books.%' AND h.text ILIKE '%Light%'"""
case = QueryCase('books-heading-search','Heading selection','URL-scoped substring search',
                 'optimizer',False,(None,),'4GB',20000,sql,Path('.'),seconds=120)
d = None
try:
    d = _connection(case)
    d.execute('BEGIN TRANSACTION')
    with deadline(d,20):
        start = perf_counter()
        keys = [r[0] for r in d.execute("SELECT DISTINCT content_id FROM public_v1.capture WHERE requested_url ILIKE '%books.%' AND content_id IS NOT NULL LIMIT 1025").fetchall()]
        assert len(keys) <= 1024
        print(json.dumps({'selected':len(keys),'selection_ms':(perf_counter()-start)*1000}),flush=True)
    from sqlglot import exp
    union = ' UNION ALL '.join('SELECT * FROM public_v1.html_heading WHERE content_id = '+exp.convert(k).sql(dialect='duckdb') for k in keys)
    candidate = replace(case,sql=sql.replace('public_v1.html_heading h','('+union+') h'))
    if os.environ.get('PROBE_SHAPE') == 'materialized_membership':
        literals = ','.join(exp.convert(k).sql(dialect='duckdb') for k in keys)
        scoped = 'WITH selected_headings AS MATERIALIZED (SELECT * FROM public_v1.html_heading WHERE list_contains(['+literals+'], content_id)) '
        candidate = replace(case,sql=scoped+sql.replace('public_v1.html_heading h','selected_headings h'),seconds=60)

    variants = [('candidate', candidate)] if os.environ.get('PROBE_PROFILE') else [('candidate', candidate), ('baseline', case)]
    for label,item in variants:
        try:
            m = asdict(_measure(d,item,None,1 if os.environ.get('PROBE_PROFILE') else 0))
            print(json.dumps({'variant':label,'measurement':m}),flush=True)
        except Exception as e:
            print(json.dumps({'variant':label,'failure':type(e).__name__}),flush=True)
            break
except Exception as e:
    print(json.dumps({'failure':type(e).__name__}),flush=True)
finally:
    if d is not None:
        d.close()
