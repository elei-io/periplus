"""Check public document identities in isolated databases on local Compose ClickHouse."""
from pathlib import Path
import subprocess,json,uuid
root=Path(__file__).resolve().parents[1] / 'packages/periplus/src/periplus'
suffix=uuid.uuid4().hex
m='material_'+suffix;q='query_'+suffix
def sql(s):
 p=subprocess.run(['docker','exec','-i','periplus-clickhouse','clickhouse-client','--multiquery'],input=s,text=True,capture_output=True)
 if p.returncode: raise RuntimeError(p.stderr)
 return p.stdout
try:
 sql((root/'materialization/schema.sql').read_text().replace('material',m))
 sql((root/'platform/clickhouse/public.sql').read_text().replace('material.',m+'.').replace('public_v1',q))
 sql(f"INSERT INTO {m}.html_documents (document_id,content_id,document_text,elements) VALUES (unhex(repeat('a',64)),unhex(repeat('c',64)),'é',[(0,NULL,1,0,0,'p',NULL,map(),'é',0,1)]),(unhex(repeat('b',64)),unhex(repeat('c',64)),'Ã©',[(0,NULL,1,0,0,'p',NULL,map(),'Ã©',0,2)]);")
 cases=[('HTTP://EXAMPLE.COM:80?x=1#frag',None,'http://example.com/?x=1','a'),('https://old.test/','HTTPS://EXAMPLE.COM:443/final#x','https://example.com/final','a'),('http://[::1]:80',None,'http://[::1]/','b')]
 for i,(requested,effective,expected,doc) in enumerate(cases):
  eff='NULL' if effective is None else "'"+effective+"'"
  sql(f"INSERT INTO {m}.captures (capture_id,document_id,content_id,requested_url,effective_url,completeness,encoding) VALUES ('00000000-0000-0000-0000-{i+1:012d}',unhex(repeat('{doc}',64)),unhex(repeat('c',64)),'{requested}',{eff},'complete','utf-8');")
 rows=json.loads(sql(f'SELECT * FROM {q}.capture ORDER BY capture_id FORMAT JSON'))
 assert [x['name'] for x in rows['meta']]==['capture_id','url','captured_at','http_status_code','document_id','byte_length','encoding'],rows
 assert [x['url'] for x in rows['data']]==[x[2] for x in cases],rows
 assert sql(f'SELECT count() FROM {q}.html_element').strip()=='2'
 assert sql(f'SELECT count() FROM {q}.capture c JOIN {q}.html_element e USING(document_id)').strip()=='3'
 assert sql(f'SELECT count() FROM {q}.capture WHERE url NOT IN (SELECT url FROM {q}.page)').strip()=='0'
 print('PASS: seven capture columns, URL normalization/fallback, two interpretations of same bytes, document joins without fanout, page membership')
finally:
 sql(f'DROP DATABASE IF EXISTS {q}; DROP DATABASE IF EXISTS {m};')
