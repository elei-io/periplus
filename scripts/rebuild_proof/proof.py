"""Isolated rebuild protocol proof; run with --run against local disposable services.

Uses the real Periplus HTML parser, link projection, SQL validator and CH client.
The small storage/control adapters here are experiment code, not deployed services.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from dataclasses import asdict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import secrets
import sys
from tempfile import TemporaryDirectory
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

import nats
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy, DiscardPolicy, RetentionPolicy, StorageType, StreamConfig
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import create_engine, text
import sqlglot
from sqlglot import exp

from periplus.ingestion.objects.store import FileObjectStore
from periplus.materialization.dom.encoder import ElementRow
from periplus.materialization.dom.links import links_from_elements
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.html_content import html_content
from periplus.platform.clickhouse import ClickHouseClient, ClickHouseConfig, ClickHouseError, connect_clickhouse
from periplus.platform.config import get_str
from periplus.platform.postgres.session import get_database_url
from periplus.query.models import QueryRequest
from periplus.query.service import public_sql


def encoded(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value: object) -> str:
    return sha256(encoded(value).encode()).hexdigest()


class Input(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    id: int
    content_id: str
    object_key: str
    page_url: str


class Proof:
    def __init__(self, name: str, root: str):
        if not re.fullmatch(r'rebuild_proof_[0-9a-f]{12}', name):
            raise ValueError('Only generated proof namespaces are allowed')
        self.name = name
        self.root = Path(root)
        self.ch = connect_clickhouse()
        self.pg = create_engine(get_database_url(), pool_size=4, max_overflow=0)
        self.raw = FileObjectStore(self.root)
        self.parse_count = 0
        self.write_count = 0

    def sql(self, statement: str, **parameters):
        with self.pg.begin() as connection:
            return connection.execute(text(statement), parameters)

    def install(self):
        self.ch.execute(f'CREATE DATABASE {self.name}')
        self.sql(f'CREATE SCHEMA {self.name}')
        self.sql(f'CREATE TABLE {self.name}.run (id int PRIMARY KEY, phase text, revision int, protected boolean, consumer_created text)')
        self.sql(f"INSERT INTO {self.name}.run VALUES (1,'building',1,true,NULL)")
        self.sql(f'CREATE TABLE {self.name}.ranges (id int PRIMARY KEY, cursor bigint, done boolean, revision int)')
        self.sql(f'INSERT INTO {self.name}.ranges VALUES (1,0,false,1)')
        self.sql(f'CREATE TABLE {self.name}.publication (id int PRIMARY KEY, target text, revision int)')
        self.sql(f"INSERT INTO {self.name}.publication VALUES (1,'a',1)")
        self.ch.execute(f'CREATE TABLE {self.name}.source (id UInt64, content_id String, object_key String, page_url String, evidence_digest String) ENGINE=MergeTree ORDER BY id')
        for target, version in [('a',1),('b',2),('c',2),('bench',2)]:
            self.ch.execute(f'CREATE TABLE {self.name}.{target}_content (content_id String, payload String, output_digest String) ENGINE=MergeTree ORDER BY content_id')
            self.ch.execute(f'CREATE TABLE {self.name}.{target}_visits (id UInt64, content_id String, evidence_digest String, links Array(Tuple(target_url String)), output_digest String) ENGINE=MergeTree ORDER BY id')
            self.ch.execute(f'''CREATE VIEW {self.name}.{target}_capture SQL SECURITY DEFINER AS
                SELECT v.id AS capture_id,v.content_id AS content_id,{version} AS revision
                FROM {self.name}.{target}_visits v INNER JOIN {self.name}.source s ON s.id=v.id AND s.content_id=v.content_id AND s.evidence_digest=v.evidence_digest
                INNER JOIN {self.name}.{target}_content d ON d.content_id=v.content_id''')
            self.ch.execute(f'''CREATE VIEW {self.name}.{target}_html_element SQL SECURITY DEFINER AS
                SELECT content_id,JSONExtractString(payload,'text') AS text,{version} AS revision
                FROM {self.name}.{target}_content WHERE content_id IN (SELECT content_id FROM {self.name}.{target}_capture)''')
            self.ch.execute(f'''CREATE VIEW {self.name}.{target}_link SQL SECURITY DEFINER AS
                SELECT id AS capture_id,link.target_url AS target_url,{version} AS revision
                FROM {self.name}.{target}_visits ARRAY JOIN links AS link
                WHERE id IN (SELECT capture_id FROM {self.name}.{target}_capture)''')

    def insert(self, table: str, rows: list[dict]):
        if rows:
            if table=='source':
                rows=[{**row,'evidence_digest':digest(row)} for row in rows]
            self.ch.execute(f'INSERT INTO {self.name}.{table} FORMAT JSONEachRow',
                            data=('\n'.join(encoded(row) for row in rows)+'\n').encode())
            self.write_count += 1

    def inputs(self, after: int = 0, limit: int = 64) -> list[Input]:
        rows = self.ch.query(f'SELECT id,content_id,object_key,page_url FROM {self.name}.source WHERE id>{{after:UInt64}} ORDER BY id LIMIT {{limit:UInt64}}',
                             parameters={'after':str(after),'limit':str(limit)})['data']
        return [Input.model_validate(row) for row in rows]

    def materialize(self, target: str, items: list[Input], crash: str | None = None):
        """Single writer per target in this proof; production identity claims tested separately."""
        if not items:
            return
        hashes = sorted({item.content_id for item in items})
        existing = self.ch.query(f'SELECT * FROM {self.name}.{target}_content WHERE content_id IN {{keys:Array(String)}}',
                                  parameters={'keys':exp.convert(hashes).sql(dialect='clickhouse')})['data']
        cache = {row['content_id']:json.loads(row['payload']) for row in existing}
        if len(cache) != len(existing):
            raise AssertionError('Duplicate content')
        new_content = []
        for item in items:
            if item.content_id in cache:
                continue
            with self.raw.open(item.object_key) as handle:
                source = handle.read(1024*1024+1)
            if len(source)>1024*1024 or sha256(source).hexdigest()!=item.content_id:
                raise ValueError('Raw input size/hash mismatch')
            nodes,elements = parse_document(source)
            html = html_content(item.content_id,nodes,elements)
            payload = {'text':html.document_text,'elements':[asdict(element) for element in elements]}
            cache[item.content_id] = payload
            new_content.append({'content_id':item.content_id,'payload':encoded(payload),'output_digest':digest(payload)})
            self.parse_count += 1
            with (self.root/'parse_events.jsonl').open('a') as log:
                log.write(encoded({'target':target,'content':item.content_id})+'\n')
        self.insert(target+'_content',new_content)
        if crash=='content':
            os._exit(31)
        previous = self.ch.query(f'SELECT id,output_digest FROM {self.name}.{target}_visits WHERE id IN {{ids:Array(UInt64)}}',
                                 parameters={'ids':encoded([i.id for i in items])})['data']
        by_id = {row['id']:row['output_digest'] for row in previous}
        if len(by_id)!=len(previous):
            raise AssertionError('Duplicate visit')
        new_visits=[]
        expected_visits={}
        for item in items:
            elements = [ElementRow(**row) for row in cache[item.content_id]['elements']]
            grouped = links_from_elements(elements,page_url=item.page_url)
            links = [{'target_url':row['target_url']} for row in grouped['internal']+grouped['external']]
            row = {'id':item.id,'content_id':item.content_id,'evidence_digest':digest(item.model_dump()),'links':links}
            row['output_digest']=digest(row)
            expected_visits[item.id]=row['output_digest']
            if item.id in by_id:
                if by_id[item.id]!=row['output_digest']:
                    raise ValueError('Conflicting immutable visit')
            else:
                new_visits.append(row)
        self.insert(target+'_visits',new_visits)
        verified = self.ch.query(f'SELECT id,output_digest FROM {self.name}.{target}_visits WHERE id IN {{ids:Array(UInt64)}}',
                                 parameters={'ids':encoded([i.id for i in items])})['data']
        assert len(verified)==len(items)
        assert {row['id']:row['output_digest'] for row in verified}==expected_visits
        if crash=='visits':
            os._exit(32)

    def page(self, crash: str | None = None, after_publish: Callable[[], None] | None = None):
        row=self.sql(f'SELECT cursor,revision FROM {self.name}.ranges WHERE id=1').one()
        items=self.inputs(row.cursor,2)
        self.materialize('b',items,crash)
        if after_publish is not None:
            after_publish()
        cursor=items[-1].id if items else row.cursor
        result=self.sql(f'''UPDATE {self.name}.ranges SET cursor=:cursor,done=:done
            WHERE id=1 AND cursor=:previous AND revision=:revision
            AND EXISTS (SELECT 1 FROM {self.name}.run WHERE id=1 AND phase='building' AND revision=:revision)''',
            cursor=cursor,done=not items,previous=row.cursor,revision=row.revision)
        if result.rowcount!=1:
            raise RuntimeError('Fenced checkpoint')
        if crash=='checkpoint':
            os._exit(33)

    async def verify_ready(self, js, stream: str, barrier: int):
        expected=self.sql(f'SELECT consumer_created FROM {self.name}.run WHERE id=1').scalar_one()
        candidate=await js.consumer_info(stream,'candidate')
        if str(candidate.created)!=expected:
            raise RuntimeError('consumer_incarnation_changed')
        if not self.sql(f'SELECT bool_and(done) FROM {self.name}.ranges').scalar_one():
            raise RuntimeError('historical_ranges_pending')
        ingestion=await js.consumer_info(stream,'ing')
        if min(candidate.ack_floor.stream_seq,ingestion.ack_floor.stream_seq)<barrier:
            raise RuntimeError('catchup_incomplete')
        self.sql(f"UPDATE {self.name}.run SET phase='ready' WHERE id=1 AND phase IN ('building','ready')")

    def rows(self, target: str):
        return self.ch.query(f'SELECT * FROM {self.name}.{target}_visits ORDER BY id')['data']

    def binding(self, sql: str, target: str) -> str:
        if target not in {'a','b'}:
            raise ValueError('Unpublished target')
        tree=sqlglot.parse_one(public_sql(QueryRequest(sql=sql)),read='clickhouse')
        for table in tree.find_all(exp.Table):
            if table.db=='public_v1':
                if not table.alias:
                    table.set('alias',exp.TableAlias(this=exp.to_identifier(table.name)))
                table.set('db',exp.to_identifier(self.name))
                table.set('this',exp.to_identifier(target+'_'+table.name))
        for column in tree.find_all(exp.Column):
            if column.db=='public_v1':
                column.set('db',None)
        return tree.sql(dialect='clickhouse')

    def close(self):
        self.ch.close();self.pg.dispose()


async def child(name: str, root: str, stream: str, subject: str, crash: str):
    p=Proof(name,root)
    nc=await nats.connect(get_str('PERIPLUS_NATS_URL'))
    sub=await nc.jetstream().pull_subscribe(subject,durable='range',stream=stream)
    message,=await sub.fetch(1,timeout=5)
    await asyncio.to_thread(p.page,crash)
    await message.ack_sync()
    p.close();await nc.close()


async def run() -> dict:
    for url in [get_str('PERIPLUS_CLICKHOUSE_URL'),get_str('PERIPLUS_NATS_URL'),get_database_url()]:
        if urlsplit(url).hostname not in {'localhost','127.0.0.1','::1'}:
            raise ValueError('Proof is restricted to explicitly local stores')
    started=time.monotonic()
    name='rebuild_proof_'+uuid4().hex[:12]
    streams=[];user=name+'_reader';user_created=False
    nc=await nats.connect(get_str('PERIPLUS_NATS_URL'));js=nc.jetstream()
    results={}
    with TemporaryDirectory(prefix='periplus-rebuild-proof-') as root:
        p=Proof(name,root)
        try:
            p.install()
            event_stream=name.upper();subject='proof.'+name+'.events'
            await js.add_stream(StreamConfig(name=event_stream,subjects=[subject],storage=StorageType.FILE,
                retention=RetentionPolicy.INTEREST,discard=DiscardPolicy.NEW,max_bytes=1024*1024));streams.append(event_stream)
            async def consumer(durable, policy=DeliverPolicy.ALL):
                await js.add_consumer(event_stream,ConsumerConfig(durable_name=durable,filter_subject=subject,
                    ack_policy=AckPolicy.EXPLICIT,deliver_policy=policy,ack_wait=60))
                return await js.pull_subscribe(subject,durable=durable,stream=event_stream)
            ing=await consumer('ing');old=await consumer('old')
            fixtures={}
            for identity,group in [(10,0),(20,0),(30,1),(40,2),(50,3),(5,3)]:
                source=f'<meta charset="utf-8"><h1>Group {group} 猫</h1><a href="next">Next</a>'.encode()
                h=sha256(source).hexdigest();key='objects/'+h
                p.raw.put_if_absent(key,BytesIO(source))
                fixtures[identity]=Input(id=identity,content_id=h,object_key=key,page_url=f'https://example.com/{identity}/')
            held_ing=[];held_old=[]
            for identity in [10,20,30,40]:
                item=fixtures[identity]
                await js.publish(subject,item.model_dump_json().encode())
                im,=await ing.fetch(1,timeout=2);om,=await old.fetch(1,timeout=2)
                if identity in {10,30}:
                    p.insert('source',[item.model_dump()]);await im.ack_sync()
                else:held_ing.append(im)
                if identity in {10,20}:
                    p.materialize('a',[item]);await om.ack_sync()
                else:held_old.append(om)
            candidate=await consumer('candidate')
            new_only=await consumer('new_only',DeliverPolicy.NEW)
            # Simulate controller losing its response after consumer creation: bind the same durable.
            before=(await js.consumer_info(event_stream,'candidate')).created
            p.sql(f'UPDATE {name}.run SET consumer_created=:created WHERE id=1',created=str(before))
            rebound=await js.pull_subscribe(subject,durable='candidate',stream=event_stream)
            assert (await js.consumer_info(event_stream,'candidate')).created==before
            candidate=rebound
            range_stream=name.upper()+'_RANGE';range_subject='proof.'+name+'.range'
            await js.add_stream(name=range_stream,subjects=[range_subject],storage=StorageType.FILE);streams.append(range_stream)
            await js.add_consumer(range_stream,ConsumerConfig(durable_name='range',filter_subject=range_subject,
                ack_policy=AckPolicy.EXPLICIT,ack_wait=0.2))
            await js.publish(range_subject,b'{"range":1}')
            crash_results=[]
            for point,code,expected_cursor in [('content',31,0),('visits',32,0),('checkpoint',33,30)]:
                proc=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve()),'--child',
                    name,root,range_stream,range_subject,point,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                try:_,stderr=await asyncio.wait_for(proc.communicate(),timeout=30)
                except BaseException:
                    if proc.returncode is None:proc.kill();await proc.wait()
                    raise
                assert proc.returncode==code,stderr.decode()
                cursor=p.sql(f'SELECT cursor FROM {name}.ranges WHERE id=1').scalar_one()
                assert cursor==expected_cursor
                if point=='content':
                    assert p.ch.query(f'SELECT count() AS n FROM {name}.b_capture')['data'][0]['n']==0
                crash_results.append({'point':point,'exit':code,'durable_cursor':cursor})
            sub=await js.pull_subscribe(range_subject,durable='range',stream=range_stream)
            message,=await sub.fetch(1,timeout=5)
            assert message.metadata.num_delivered>=4
            p.page();await message.ack_sync()
            assert p.sql(f'SELECT done FROM {name}.ranges WHERE id=1').scalar_one()
            results['worker_crashes']=crash_results
            for identity in [50,5]:
                await js.publish(subject,fixtures[identity].model_dump_json().encode())
                held_ing.extend(await ing.fetch(1,timeout=2));held_old.extend(await old.fetch(1,timeout=2))
            barrier=await js.publish(subject,b'{"barrier":true}')
            new_messages=await new_only.fetch(3,timeout=3)
            new_ids=[]
            for message in new_messages:
                payload=json.loads(message.data)
                if 'id' in payload:new_ids.append(payload['id'])
                await message.ack_sync()
            missed=sorted(set(fixtures)-{10,30}-set(new_ids))
            assert missed==[20,40]
            results['negative_control_deliver_new_misses']=missed
            cm=await candidate.fetch(6,timeout=3)
            seen=[];blocked=None
            missing=p.root/fixtures[40].object_key
            saved=missing.read_bytes();missing.unlink()
            for message in cm:
                payload=json.loads(message.data)
                if 'barrier' in payload:
                    await message.ack_sync();continue
                item=Input.model_validate(payload);seen.append(item.id)
                try:p.materialize('b',[item])
                except FileNotFoundError:
                    assert item.id==40;blocked=message
                else:await message.ack_sync()
            assert sorted(seen)==[5,20,30,40,50]
            floor=(await js.consumer_info(event_stream,'candidate')).ack_floor.stream_seq
            assert floor<barrier.seq and blocked is not None
            try:await p.verify_ready(js,event_stream,barrier.seq)
            except RuntimeError as exc:assert str(exc)=='catchup_incomplete'
            else:raise AssertionError('Incomplete target became ready')
            assert p.sql(f'SELECT phase FROM {name}.run WHERE id=1').scalar_one()=='building'
            assert p.ch.query(f'SELECT count() AS n FROM {name}.b_capture WHERE capture_id=20')['data'][0]['n']==0
            p.raw.put_if_absent(fixtures[40].object_key,BytesIO(saved))
            p.materialize('b',[fixtures[40]]);await blocked.ack_sync()
            for message in held_ing:
                p.insert('source',[Input.model_validate_json(message.data).model_dump()]);await message.ack_sync()
            for message in held_old:
                p.materialize('a',[Input.model_validate_json(message.data)]);await message.ack_sync()
            for sub in [ing,old]:
                marker,=await sub.fetch(1,timeout=2);assert json.loads(marker.data)=={'barrier':True};await marker.ack_sync()
            floors={d:(await js.consumer_info(event_stream,d)).ack_floor.stream_seq for d in ['ing','candidate']}
            assert all(f>=barrier.seq for f in floors.values())
            await p.verify_ready(js,event_stream,barrier.seq)
            assert p.sql(f'SELECT phase FROM {name}.run WHERE id=1').scalar_one()=='ready'
            results['coverage']={'candidate_ids':seen,'barrier':barrier.seq,'blocked_floor':floor,'complete_floors':floors,
                'source_ids':[i.id for i in p.inputs()],'target_ids':[r['id'] for r in p.rows('b')]}
            assert [r['id'] for r in p.rows('b')]==sorted(fixtures)
            p.materialize('c',p.inputs())
            assert p.rows('b')==p.rows('c')
            assert p.ch.query(f'SELECT * FROM {name}.b_content ORDER BY content_id')['data']==p.ch.query(f'SELECT * FROM {name}.c_content ORDER BY content_id')['data']
            parses=[json.loads(line) for line in (p.root/'parse_events.jsonl').read_text().splitlines()]
            results['content_reuse']={'candidate_parse_calls':sum(row['target']=='b' for row in parses),'distinct_content':len({i.content_id for i in fixtures.values()})}
            assert results['content_reuse']['candidate_parse_calls']==results['content_reuse']['distinct_content']==4
            try:p.materialize('b',[fixtures[10].model_copy(update={'page_url':'https://example.com/conflict/'})])
            except ValueError:pass
            else:raise AssertionError('Conflicting replay accepted')
            for row in p.rows('b'):
                assert row['links']==[{'target_url':f'https://example.com/{row["id"]}/next'}]
            results['offline_equality_and_conflict_rejection']=True
            # Prepare old and candidate query views; grants expose only these views.
            password=secrets.token_urlsafe(32)
            p.ch.execute('CREATE USER {user:Identifier} IDENTIFIED BY {password:String} SETTINGS readonly=1,max_execution_time=10,max_threads=1',
                         parameters={'user':user,'password':password});user_created=True
            p.ch.execute(f'ALTER USER {user} SETTINGS max_execution_time=45 MIN 0.01 MAX 45 CHANGEABLE_IN_READONLY, max_threads=1 READONLY, max_memory_usage=268435456 READONLY, output_format_json_quote_64bit_integers=0 READONLY')
            for target in ['a','b']:
                for relation in ['capture','html_element','link']:
                    p.ch.execute(f'GRANT SELECT ON {name}.{target}_{relation} TO {user}')
            reader_config=ClickHouseConfig.from_env().model_copy(update={'username':user,'password':SecretStr(password),'query_only':True})
            reader=ClickHouseClient(reader_config)
            try:
                try:reader.query(f'SELECT * FROM {name}.source')
                except ClickHouseError as exc:assert exc.code=='497'
                else:raise AssertionError('Private evidence readable')
                sql='SELECT c.capture_id AS capture_id,c.revision AS c_rev,e.revision AS e_rev,l.revision AS l_rev,e.text,l.target_url FROM public_v1.capture c JOIN public_v1.html_element e USING(content_id) JOIN public_v1.link l USING(capture_id) ORDER BY capture_id'
                binding_cases=[
                    'SELECT capture_id FROM public_v1.capture ORDER BY capture_id',
                    'SELECT capture.capture_id FROM public_v1.capture ORDER BY capture.capture_id',
                    'SELECT public_v1.capture.capture_id FROM public_v1.capture ORDER BY capture_id',
                    'WITH picked AS (SELECT capture_id FROM public_v1.capture) SELECT capture_id FROM picked ORDER BY capture_id',
                    'WITH capture AS (SELECT capture_id FROM public_v1.capture) SELECT capture_id FROM capture ORDER BY capture_id',
                    'SELECT capture_id FROM public_v1.capture WHERE capture_id<30 UNION ALL SELECT capture_id FROM public_v1.capture WHERE capture_id>=30 ORDER BY capture_id',
                ]
                for case in binding_cases:
                    assert sorted(r['capture_id'] for r in reader.query(p.binding(case,'b'))['data'])==sorted(fixtures), case
                results['query_binding_shapes']=len(binding_cases)
                pinned=p.binding(sql,'a')
                assert p.sql(f"UPDATE {name}.publication SET target='b',revision=revision+1 WHERE id=1 AND revision=1").rowcount==1
                assert p.sql(f"UPDATE {name}.publication SET target='a' WHERE id=1 AND revision=1").rowcount==0
                assert all(r['c_rev']==r['e_rev']==r['l_rev']==1 for r in reader.query(pinned)['data'])
                assert all(r['c_rev']==r['e_rev']==r['l_rev']==2 for r in reader.query(p.binding(sql,'b'))['data'])
            finally:reader.close()
            ready=threading.Barrier(3)
            def queries():
                r=ClickHouseClient(reader_config);counts={'a':0,'b':0}
                try:
                    ready.wait(timeout=10)
                    for _ in range(60):
                        target=p.sql(f'SELECT target FROM {name}.publication WHERE id=1').scalar_one()
                        bound=p.binding(sql,target)
                        time.sleep(0.002)
                        rows=r.query(bound)['data'];version=1 if target=='a' else 2
                        assert 6<=len(rows)<=8
                        assert set(fixtures)<={x['capture_id'] for x in rows}
                        assert all(x['c_rev']==x['e_rev']==x['l_rev']==version for x in rows)
                        counts[target]+=1
                    return counts
                finally:r.close()
            live_times=[]
            overlap_backfill=0
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(queries) for _ in range(2)];ready.wait(timeout=10)
                for i in range(40):
                    if i<4:
                        items=[fixtures[10].model_copy(update={'id':10000+i*64+j,'page_url':f'https://example.com/backfill/{i}/{j}/'}) for j in range(64)]
                        p.materialize('c',items);overlap_backfill+=len(items)
                    if i in {5,10}:
                        live_started=time.monotonic()
                        item=fixtures[10].model_copy(update={'id':61 if i==5 else 62,'page_url':f'https://example.com/live/{i}/'})
                        await js.publish(subject,item.model_dump_json().encode())
                        im,=await ing.fetch(1,timeout=2)
                        p.insert('source',[item.model_dump()]);await im.ack_sync()
                        for sub,target in [(old,'a'),(candidate,'b')]:
                            delivery,=await sub.fetch(1,timeout=2)
                            assert Input.model_validate_json(delivery.data)==item
                            p.materialize(target,[item]);await delivery.ack_sync()
                        live_times.append(round((time.monotonic()-live_started)*1000,2))
                    p.sql(f'UPDATE {name}.publication SET target=:target,revision=revision+1 WHERE id=1',target='a' if i%2 else 'b')
                    await asyncio.sleep(0.01)
                counts=[f.result(timeout=30) for f in futures]
            assert sum(c['a'] for c in counts)>0 and sum(c['b'] for c in counts)>0
            results['concurrent_work']={'live_visits':2,'live_completion_ms':live_times,'backfill_visits':overlap_backfill}
            results['activation']={'joined_queries':120,'switches':40,'versions_observed':counts,'mixed_versions':0,'stale_activation_rejected':True,'private_source_denied':True}
            # Recreating a durable with the same name must not silently reset coverage.
            await js.delete_consumer(event_stream,'candidate')
            candidate=await consumer('candidate')
            try:await p.verify_ready(js,event_stream,barrier.seq)
            except RuntimeError as exc:assert str(exc)=='consumer_incarnation_changed'
            else:raise AssertionError('Replacement consumer accepted as the original')
            assert p.sql(f'SELECT protected FROM {name}.run WHERE id=1').scalar_one()
            results['activation_guards']={'incomplete_catchup_rejected':True,'recreated_consumer_rejected':True}
            # Old publication is selected and all query threads have drained before cancelling B.
            assert p.sql(f'SELECT target FROM {name}.publication WHERE id=1').scalar_one()=='a'
            p.sql(f"UPDATE {name}.run SET phase='building' WHERE id=1")
            p.page()  # Verify the already complete 40/50 page.
            extra=fixtures[10].model_copy(update={'id':60,'page_url':'https://example.com/cancel/'})
            p.insert('source',[extra.model_dump()])
            published=threading.Event();release=threading.Event()
            def hold():
                published.set()
                if not release.wait(timeout=10):raise TimeoutError('Controller did not release worker')
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending=pool.submit(p.page,None,hold)
                try:
                    assert published.wait(timeout=10)
                    p.sql(f"UPDATE {name}.run SET phase='cancelling',revision=2 WHERE id=1")
                    assert not pending.done()
                    assert p.sql(f'SELECT protected FROM {name}.run WHERE id=1').scalar_one()
                    assert p.raw.exists(extra.object_key)
                finally:release.set()
                try:pending.result(timeout=10)
                except RuntimeError as exc:assert str(exc)=='Fenced checkpoint'
                else:raise AssertionError('Cancelled worker advanced checkpoint')
            assert p.sql(f'SELECT cursor FROM {name}.ranges WHERE id=1').scalar_one()==50
            await js.delete_consumer(event_stream,'candidate')
            p.sql(f"UPDATE {name}.run SET phase='cancelled',protected=false WHERE id=1 AND phase='cancelling'")
            results['cancellation']={'inflight_write_drained':True,'stale_checkpoint_rejected':True,'protection_held_until_drain':True}
            # A bounded throughput observation, not a capacity claim.
            bench=[]
            for i in range(512):
                seed=fixtures[10 if i%2 else 30]
                bench.append(seed.model_copy(update={'id':1000+i,'page_url':f'https://example.com/bench/{i}/'}))
            start=time.monotonic();writes=p.write_count;parses=p.parse_count
            for offset in range(0,len(bench),64):p.materialize('bench',bench[offset:offset+64])
            results['batch_observation']={'visits':512,'batch_size':64,'seconds':round(time.monotonic()-start,3),
                'insert_requests':p.write_count-writes,'parse_calls':p.parse_count-parses}
            assert p.write_count-writes==9 and p.parse_count-parses==2
            results['elapsed_seconds']=round(time.monotonic()-started,3)
            return results
        finally:
            errors=[]
            for stream in streams:
                try:await js.delete_stream(stream)
                except Exception as exc:errors.append(('stream',type(exc).__name__))
            if user_created:
                try:p.ch.execute(f'DROP USER IF EXISTS {user}')
                except Exception as exc:errors.append(('user',type(exc).__name__))
            try:p.ch.execute(f'DROP DATABASE IF EXISTS {name} SYNC')
            except Exception as exc:errors.append(('clickhouse',type(exc).__name__))
            try:p.sql(f'DROP SCHEMA IF EXISTS {name} CASCADE')
            except Exception as exc:errors.append(('postgres',type(exc).__name__))
            p.close();await nc.close()
            if errors:raise RuntimeError(f'Proof cleanup failed: {errors}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--child',nargs=5)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.child:asyncio.run(child(*args.child));return
    if not args.run:parser.error('--run is required')
    result=asyncio.run(run())
    value=json.dumps(result,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(value+'\n')
    print(value)


if __name__=='__main__':main()
