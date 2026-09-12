"""Disposable production-shaped publication and native compaction measurement."""
import os
os.environ['PERIPLUS_CONTROL_DATABASE_URL']='postgresql://unused:unused@127.0.0.1:1/unused'
os.environ['PERIPLUS_MATERIALIZER_PARSER_PROCESSES']='2'
import sys,json,time,resource,random,hashlib
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from datetime import UTC, datetime
from dataclasses import asdict,replace
import unittest
import zstandard as zstd
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'packages/periplus/tests'))
from operational_state_fixture import operational_state
from periplus.materialization.batch import prepare_batch,commit_prepared_batch
from periplus.materialization.registry import PROJECTIONS,REGISTRY_DIGEST
from periplus.materialization.models import MaterializationRunRecord, MaterializationBatchRecord
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.query.benchmarking import QueryCase,_measure

def main():
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 out=args.output.resolve();out.mkdir(exist_ok=False);os.environ['PERIPLUS_DUCKLAKE_DATA_PATH']=str(out/'data');os.environ['PERIPLUS_DUCKLAKE_METADATA_PATH']=str(out/'metadata.duckdb')
 fixture=args.fixture;manifest=json.loads((fixture/'manifest.json').read_text());random.Random(2026).shuffle(manifest)
 test=unittest.TestCase();sessions=operational_state(test)
 cfg=CatalogueConfig('periplus',str(out/'metadata.duckdb'),str(out/'data'),'ducklake')
 report={'batches':[],'registry':REGISTRY_DIGEST}
 try:
  with Catalogue(cfg) as lake:
   lake.bootstrap();c=lake.trusted_connection;c.execute("SET threads=2");c.execute("SET memory_limit='4GB'");c.execute(f"SET temp_directory='{out}/spill'");c.execute("SET max_temp_directory_size='8GB'")
   run=SimpleNamespace(id=uuid4(),batch_size=50,registry_digest=REGISTRY_DIGEST,generation_tables={s.name:s.name for s in PROJECTIONS})
   def read(key):return zstd.ZstdDecompressor().decompress((fixture/'objects'/key).read_bytes())
   repo=SimpleNamespace(store=None,iter_bytes=lambda key:iter([read(key)]),read=lambda key:read(key).decode())
   now=datetime.now(UTC)
   for item in manifest:
    item['visit_id']=str(uuid4());item['document_id']=str(uuid4())
   c.execute("BEGIN")
   c.executemany("INSERT INTO ingest.visits(visit_id,document_id,requested_url,outcome,admitted_at,finished_at) VALUES (?,?,'https://fixture.invalid/','succeeded',?,?)",[(m['visit_id'],m['document_id'],now,now) for m in manifest])
   c.executemany("INSERT INTO ingest.documents(document_id,visit_id,content_sha256,object_key,storage_encoding,content_bytes,detected_media_type,observed_at,representation,stored_bytes) VALUES (?,?,?,?,'zstd',?,'text/html',?,'rendered_html',?)",[(m['document_id'],m['visit_id'],m['id'],m['file'],m['bytes'],now,m['compressed_bytes']) for m in manifest])
   c.execute("COMMIT")
   snapshot=lake.latest_snapshot()
   with sessions.begin() as session:
    session.add(MaterializationRunRecord(id=run.id,status='running',source_snapshot=snapshot,covered_snapshot=snapshot,generation_tables=run.generation_tables,registry_digest=REGISTRY_DIGEST,batch_size=50,created_at=now))
   total=time.perf_counter()
   for offset in range(0,len(manifest),50):
    group=manifest[offset:offset+50]
    batch=SimpleNamespace(id=uuid4(),snapshot=snapshot,visit_ids=tuple(m['visit_id'] for m in group))
    with sessions.begin() as session:
     session.add(MaterializationBatchRecord(id=batch.id,run_id=run.id,ordinal=offset//50,snapshot=snapshot,visit_ids=list(batch.visit_ids)))
    start=time.perf_counter();prepared=prepare_batch(lake,repo,run,batch);prepared_s=time.perf_counter()-start
    start=time.perf_counter();commit_prepared_batch(lake,run,batch,prepared);commit_s=time.perf_counter()-start
    assert prepare_batch(lake,repo,run,batch).already_applied
    report['batches'].append({'contents':len(group),'prepare_s':prepared_s,'commit_s':commit_s,'output_bytes':prepared.output_bytes,'output_rows':prepared.output_rows,'files':sum(len(v) for v in prepared.files.values())});print(json.dumps(report['batches'][-1]),flush=True)
   report['build_s']=time.perf_counter()-total
   cid=manifest[0]['id'];sql=f"SELECT node_index,tag,text FROM public_v1.html_element WHERE content_id='{cid}' ORDER BY node_index"
   case=QueryCase('canonical-content','Content lookup','Read complete elements','schema/catalogue',True,(None,),'4GB',1000,sql,out,60)
   report['lookup_before']=asdict(_measure(c,case,None,1))
   report['discovery']=asdict(_measure(c,replace(case,identifier='canonical-discovery',sql="SELECT count(*) FROM public_v1.html_element WHERE text ILIKE '%robot%'"),None,1))
   report['counts']={s.name:c.execute(f'SELECT count(*) FROM material.{s.name}').fetchone()[0] for s in PROJECTIONS}
   for s in PROJECTIONS:
    for sql in s.validation_queries:assert c.execute(sql).fetchone()==(0,)
   report['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
  (out/'report.json').write_text(json.dumps(report,indent=2,default=str));print('REPORT '+str(out/'report.json'),flush=True)
 finally:test.doCleanups()
if __name__=='__main__':main()
