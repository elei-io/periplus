import json
from collections import Counter,defaultdict
from unittest.mock import patch
from periplus.query import benchmarking as bench
from periplus.query.service import QueryService,QueryRequest,QueryMode
from periplus.query.prose_matches import ProseMatches
from periplus.operations.access.schemas import QueryLimits
sql="SELECT c.effective_url,left(p.text,1500) AS preview FROM public_v1.capture c JOIN public_v1.prose p USING(content_id) WHERE regexp_matches(lower(p.text),'acquisition criteria|seeking acquisitions|add-on acquisitions|buy-and-build')"
request=QueryRequest(sql=sql)
limits=QueryLimits(max_rows=10000,max_duration_seconds=60,max_result_bytes=32*1024*1024)
original=ProseMatches.select
reference=None
snapshot=None
counts=None
def with_reference(matches,connection):
 global reference,snapshot,counts
 bound=original(matches,connection)
 assert bound is not None
 grouped=defaultdict(list)
 for key,preview in zip(bound['1'],bound['2']):
  if key is not None:grouped[key].append(preview)
 snapshot=connection.execute('SELECT id FROM ducklake_current_snapshot(?)',[bench.catalogue_config_from_env().alias]).fetchone()[0]
 captures=bench.bounded_rows(connection.execute('SELECT effective_url,content_id FROM public_v1.capture'),max_rows=250000)
 reference=Counter((url,preview) for url,key in captures for preview in grouped.get(key,[]))
 counts={'captures':len(captures),'matches':len(bound['1'])}
 return bound
for mode in [QueryMode.EXPERIMENTAL,QueryMode.STABLE]:
 service=QueryService(bench.catalogue_config_from_env(),mode=mode)
 try:
  if mode==QueryMode.EXPERIMENTAL:
   with patch.object(ProseMatches,'select',with_reference):
    result=service.execute(request,limits=limits)
   assert not result.truncated and result.source_snapshot==snapshot
   assert Counter(map(tuple,result.rows))==reference
   print(json.dumps({'mode':mode,'reference_equal':True,'snapshot':snapshot,'rows':len(result.rows),**counts}),flush=True)
  for run in range(3 if mode==QueryMode.EXPERIMENTAL else 1):
   result=service.execute(request,limits=limits)
   print(json.dumps({'mode':mode,'run':run,'ms':result.elapsed_ms,'rows':len(result.rows),'truncated':result.truncated,'snapshot':result.source_snapshot,'optimizations':result.optimizations,'types':result.types}),flush=True)
 except Exception as e:print(json.dumps({'mode':mode,'error':type(e).__name__}),flush=True)
 finally:service.close()
