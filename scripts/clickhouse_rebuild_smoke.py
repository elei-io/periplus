"""Exercise the deployed local rebuild/API/query path; never targets production.

Requires at least three real local captures. Uses homelab CDP only through the
configured crawler when the ordinary fresh-capture smoke is invoked.
"""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx
from periplus.platform.config import get_str
from periplus.platform.clickhouse import connect_clickhouse

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8000'


def local_only():
    for key in ('PERIPLUS_CLICKHOUSE_URL', 'PERIPLUS_NATS_URL', 'PERIPLUS_CONTROL_DATABASE_URL'):
        if urlsplit(get_str(key)).hostname not in ('localhost', '127.0.0.1'):
            raise RuntimeError('Rebuild smoke requires local disposable stores')


async def main():
    local_only()
    output = {}
    headers = {'Authorization': 'Bearer ' + get_str('PERIPLUS_ADMIN_API_TOKEN')}
    query_headers = {'Authorization': 'Bearer ' + get_str('PERIPLUS_QUERY_API_TOKEN')}
    async with httpx.AsyncClient(base_url=BASE, headers=headers, timeout=30) as api, httpx.AsyncClient(
            base_url='http://127.0.0.1:8010', headers=query_headers, timeout=50) as query:
        async def get(path):
            response = await api.get(path); response.raise_for_status(); return response.json()
        async def post(path, value):
            response = await api.post(path, json=value); response.raise_for_status(); return response.json()
        async def sql(text):
            response = await query.post('/query/exec', json={'sql': text})
            response.raise_for_status(); return response.json()
        before = await sql('SELECT count() FROM capture')
        if before['rows'][0][0] < 3:
            raise RuntimeError('Run three fresh-capture smokes first')
        created = await post('/operations/materializations/runs', {'page_size': 1})
        identity = created['id']; path = '/operations/materializations/runs/' + identity
        output['build_id'] = identity
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            build = await get(path)
            if build['blocker']: raise RuntimeError(str(build['blocker']))
            if build['phase'] == 'building': break
            await asyncio.sleep(0.1)
        else: raise TimeoutError('Build did not begin')
        await post(path + '/actions', {'action': 'pause'})
        paused = await get(path)
        assert paused['paused']
        # The real crawler and both material targets stay live while historical
        # scanning is paused. No fixture is represented as a real website capture.
        process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT/'scripts/clickhouse_smoke.py'),
            '--query-url', 'http://127.0.0.1:8010', cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
        if process.returncode: raise RuntimeError(stderr.decode())
        output['fresh_capture_during_pause'] = json.loads(stdout)
        await post(path + '/actions', {'action': 'resume'})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            progress = await get(path)
            if sum(row['processed'] for row in progress['ranges']) > 0:
                break
            await asyncio.sleep(0.05)
        else:
            raise TimeoutError('No non-zero durable checkpoint was recorded')
        # Stop the actual worker and inspect its durable progress while offline.
        await asyncio.to_thread(subprocess.run, ['docker','compose','stop','periplus-materializer'], cwd=ROOT, check=True, capture_output=True)
        stopped = await get(path)
        output['checkpoint_at_stop'] = sum(row['processed'] for row in stopped['ranges'])
        assert output['checkpoint_at_stop'] > 0
        try:
            result = await sql('SELECT c.capture_id, e.text FROM capture c JOIN html_element e USING(content_id) WHERE e.tag=\'h1\' ORDER BY c.capture_id')
            assert result['rows']
        finally:
            await asyncio.to_thread(subprocess.run, ['docker','compose','up','-d','--wait','periplus-materializer'], cwd=ROOT, check=True, capture_output=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            build = await get(path)
            if build['blocker']: raise RuntimeError(str(build['blocker']))
            if build['phase'] == 'ready' and build['live_pending'] == 0: break
            await asyncio.sleep(1)
        else: raise TimeoutError('Build failed to reach readiness')
        assert all(row['done'] for row in build['ranges'])
        assert build['material_floor'] >= build['barrier'] and build['ingestion_floor'] >= build['barrier']
        client = connect_clickhouse()
        try:
            source = client.query('SELECT toString(visit_id) AS id, lower(hex(evidence_sha256)) AS digest FROM ingest.visits ORDER BY id')['data']
            target = client.query(f"SELECT toString(visit_id) AS id, lower(hex(evidence_sha256)) AS digest FROM {build['material_database']}.visit_results ORDER BY id")['data']
            assert source == target
        finally: client.close()
        output['exact_covered_visits'] = len(source)
        output['barrier'] = build['barrier']
        output['floors'] = [build['ingestion_floor'], build['material_floor']]
        await post(path + '/actions', {'action': 'activate'})
        after = await sql('SELECT c.capture_id,e.text,l.target_url FROM capture c JOIN html_element e USING(content_id) JOIN link l USING(capture_id) WHERE e.tag=\'h1\' ORDER BY c.capture_id')
        assert len(after['rows']) >= before['rows'][0][0]
        context = await api.get('/internal/query-context', headers=query_headers)
        context.raise_for_status()
        assert context.json()['publication']['database'] == build['query_database']
        output['publication'] = context.json()['publication']
        output['joined_rows_after_activation'] = len(after['rows'])
        # One statement with CTE shadowing in another UNION branch must bind both
        # actual public tables. SQL execution, not only an AST assertion.
        shadow = await sql('SELECT count() AS n FROM capture UNION ALL (WITH capture AS (SELECT * FROM public_v1.capture) SELECT count() AS n FROM capture)')
        assert shadow['rows'][0] == shadow['rows'][1]
        output['cte_union_counts'] = shadow['rows']
        for endpoint in ('/sql/metadata', '/operations/storage', '/operations/ingestion'):
            await get(endpoint)
        output['operator_reads'] = 'passed'
    destination = ROOT/'.artifacts/clickhouse/deployed-rebuild.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, indent=2)+'\n')
    print(json.dumps(output))


if __name__ == '__main__':
    asyncio.run(main())
