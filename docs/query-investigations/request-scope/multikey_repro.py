import duckdb,tempfile,json
from pathlib import Path
c=duckdb.connect();c.execute("SET threads=2");p=Path(tempfile.mkdtemp(prefix='multikey-'))/'nodes.parquet'
print(json.dumps({'version':duckdb.__version__}),flush=True)
c.execute(f"COPY (SELECT md5((i//100)::VARCHAR) content_id,i%100 node_index,md5(i::VARCHAR) AS value FROM range(2000000) t(i) ORDER BY content_id,node_index) TO '{p}' (FORMAT PARQUET,ROW_GROUP_SIZE 2048)")
keys=[x[0] for x in c.execute('SELECT md5((i*147)::VARCHAR) FROM range(136) t(i)').fetchall()];lits=','.join("'"+k+"'" for k in keys);vals=','.join("('"+k+"')" for k in keys)
base=f"SELECT content_id,count(*) n,sum(length(value)) chars FROM '{p}'"
queries={'in':base+f' WHERE content_id IN ({lits}) GROUP BY content_id ORDER BY content_id','join':f'WITH keys(content_id) AS MATERIALIZED (VALUES {vals}) '+base+' SEMI JOIN keys USING(content_id) GROUP BY content_id ORDER BY content_id'}
queries['in_exact']=base+f' WHERE content_id IN ({lits}) AND list_contains([{lits}],content_id) GROUP BY content_id ORDER BY content_id'
queries['dynamic_exact']=queries['join'].replace(' GROUP BY', ' WHERE list_contains((SELECT list(content_id) FROM keys),content_id) GROUP BY')
results=[]
for opt in ['', 'in_clause']:
 c.execute("SET disabled_optimizers='"+opt+"'")
 for label,sql in queries.items():
  rows=c.execute(sql).fetchall();results.append(rows)
  profile=json.loads(c.execute('EXPLAIN (ANALYZE, FORMAT JSON) '+sql).fetchone()[1]);scans=[]
  def visit(n):
   if 'SCAN' in n.get('operator_name',''):scans.append({'operator':n.get('operator_name'),'rows':n.get('operator_cardinality'),'filters':str(n.get('extra_info',{}).get('Filters',''))[:90]})
   for ch in n.get('children',[]):visit(ch)
  visit(profile)
  print(json.dumps({'disabled':opt,'query':label,'scans':scans}),flush=True)
print(json.dumps({'equal':all(x==results[0] for x in results)}));c.close();p.unlink();p.parent.rmdir()
