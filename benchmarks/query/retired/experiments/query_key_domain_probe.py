"""Read-only experiment, NOT a production SQL optimizer.

Run inside the configured query container:
  docker compose exec -T periplus-query python - < packages/periplus/scripts/query_key_domain_probe.py

The three views are manually verified to be content-local. This harness does
not infer eligibility for arbitrary views. It preserves duplicate result rows,
compares SQL types, profiles actual operators, and never changes lake state.
"""
import json,time,threading
from collections import Counter
from importlib.resources import files
from sqlglot import parse_one,exp
from periplus.query.service import QueryService
from periplus.platform.catalogue.config import catalogue_config_from_env
q=QueryService(catalogue_config_from_env());d=q.connection
root=files('periplus.platform.catalogue').joinpath('sql/public_v1/views')

def scoped_view(view):
 tree=parse_one(root.joinpath(view+'.sql').read_text(),read='duckdb').expression
 for table in list(tree.find_all(exp.Table)):
  if table.db=='public_v1' and table.name in {'html_node','html_element'}:
   table.set('this',exp.to_identifier('scoped_'+table.name));table.set('db',None)
 return tree.sql(dialect='duckdb')

def variants(view,where):
 original=f"SELECT c.effective_url, m.* FROM prose p JOIN capture c USING(content_id) JOIN {view} m USING(content_id) WHERE p.text ILIKE ? AND {where}"
 scoped=f'''WITH selected AS MATERIALIZED (SELECT * FROM prose WHERE text ILIKE ?),
 keys AS (SELECT DISTINCT content_id FROM selected),
 scoped_html_node AS NOT MATERIALIZED (SELECT n.* FROM public_v1.html_node n SEMI JOIN keys USING(content_id)),
 scoped_html_element AS NOT MATERIALIZED (SELECT e.* FROM public_v1.html_element e SEMI JOIN keys USING(content_id))
 SELECT c.effective_url,m.* FROM selected p JOIN capture c USING(content_id)
 JOIN ({scoped_view(view)}) m USING(content_id) WHERE {where}'''
 return original,scoped

def summary(profile):
 out=[]
 def walk(n):
  op=n.get('operator_name','');info=n.get('extra_info',{})
  if op in {'DUCKLAKE_SCAN','HASH_GROUP_BY','WINDOW'}:
   out.append(dict(op=op,table=info.get('Table'),rows=n.get('operator_cardinality'),scanned=n.get('operator_rows_scanned'),files=info.get('Total Files Read'),aggregates=info.get('Aggregates')))
  for c in n.get('children',[]):walk(c)
 walk(profile);return out
results=[]
d.execute('BEGIN');snapshot=d.execute('SELECT id FROM ducklake_current_snapshot(?)',[q.alias]).fetchone()[0]
for view,where in [('html_metadata',"m.name='title'"),('html_heading','m.level=1'),('html_section','true')]:
 for needle in ['%robot%','%wild robot%','%']:
  record=dict(view=view,needle=needle,snapshot=snapshot,variants=[])
  rows_by=[]
  for label,sql in zip(['original','scoped'],variants(view,where)):
   timer=threading.Timer(25,d.interrupt);timer.start()
   try:
    t=time.monotonic();cursor=d.execute(sql,[needle]);rows=cursor.fetchall();types=[str(x[1]) for x in cursor.description];elapsed=time.monotonic()-t
    profile=json.loads(d.execute('EXPLAIN (ANALYZE, FORMAT JSON) '+sql,[needle]).fetchone()[-1])
    item=dict(label=label,elapsed=elapsed,row_count=len(rows),summary=summary(profile),sql=sql)
    record['variants'].append(item);rows_by.append((Counter(map(repr,rows)),types))
    print(view, needle, label, round(elapsed, 3), len(rows), flush=True)
   except Exception as e:
    print('ERROR',view,label,type(e).__name__,str(e)[:120],flush=True);raise
   finally:timer.cancel();timer.join()
  record['equivalent']=rows_by[0]==rows_by[1];assert record['equivalent'];results.append(record)
d.execute('ROLLBACK');q.close()
print(json.dumps(results, indent=2))
