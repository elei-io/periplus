import duckdb,tempfile,time,json
from pathlib import Path
c=duckdb.connect();c.execute('SET threads=2');p=Path(tempfile.mkdtemp(prefix='membership-scale-'))/'nodes.parquet'
try:
 c.execute(f"COPY (SELECT md5((i//20)::VARCHAR) content_id,md5(i::VARCHAR) AS value FROM range(2000000) t(i) ORDER BY content_id) TO '{p}' (FORMAT PARQUET,ROW_GROUP_SIZE 2048)")
 for size in [136,1000,10000,100000]:
  keys=[x[0] for x in c.execute('SELECT md5(i::VARCHAR) FROM range(?) t(i)',[size]).fetchall()]
  base=f"SELECT count(*),sum(length(value)) FROM '{p}' WHERE content_id IN (SELECT unnest($keys))"
  exact=base+' AND list_contains($keys,content_id)'
  results=[]
  for name,sql in [('base',base),('exact',exact)]:
   start=time.perf_counter();result=c.execute(sql,{'keys':keys}).fetchall();results.append(result)
   print(json.dumps({'keys':size,'variant':name,'ms':(time.perf_counter()-start)*1000,'count':result[0][0]}),flush=True)
  assert results[0]==results[1]
finally:c.close();p.unlink(missing_ok=True);p.parent.rmdir()
