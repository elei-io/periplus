"""Verify local Compose materializer downtime preserves accepted crawl work.

Run only against this experiment's disposable local Compose project. The worker
is restarted in finally even if an assertion fails. No volumes are deleted.
"""
import json
from pathlib import Path
import subprocess
import time

import httpx
from periplus.platform.config import get_str

ROOT = Path(__file__).resolve().parents[1]


def compose(*args: str) -> None:
    subprocess.run(['docker', 'compose', *args], cwd=ROOT, check=True, timeout=90,
                   stdout=subprocess.DEVNULL)


def main() -> None:
    with httpx.Client(base_url='http://127.0.0.1:8000', timeout=15, trust_env=False,
                      headers={'Authorization': 'Bearer '+get_str('PERIPLUS_ADMIN_API_TOKEN')}) as api, \
         httpx.Client(base_url='http://127.0.0.1:8010', timeout=30, trust_env=False,
                      headers={'Authorization': 'Bearer '+get_str('PERIPLUS_QUERY_API_TOKEN')}) as query:
        compose('stop', 'periplus-materializer')
        try:
            response = api.post('/collections', json={'specification': {
                'seed_urls': ['https://example.com/'], 'page_limit': 1, 'max_depth': 0,
                'result_max_age_seconds': 0, 'max_duration_seconds': 120}})
            response.raise_for_status()
            identity = response.json()['id']
            deadline = time.monotonic()+120
            while time.monotonic() < deadline:
                response = api.get('/collections/'+identity)
                response.raise_for_status()
                collection = response.json()
                if collection['ingested_pages'] == 1 and collection['lineage_ready']:
                    break
                time.sleep(1)
            else:
                raise AssertionError('Crawl did not finish base ingestion during materializer outage')
            assert collection['query_ready'] is False, collection
            response = api.get('/collections/'+identity+'/arrivals')
            response.raise_for_status()
            arrivals = response.json()['items']
            assert len(arrivals) == 1 and arrivals[0]['mode'] != 'reused', arrivals
            visit = arrivals[0]['observation_id']
            payload = {'sql': 'SELECT capture_id FROM public_v1.capture WHERE capture_id=?',
                       'parameters': [visit]}
            response = query.post('/query/exec', json=payload)
            response.raise_for_status()
            assert response.json()['rows'] == [], response.json()
            print(json.dumps({'stage': 'base_ingested_public_hidden', 'collection_id': identity,
                              'capture_id': visit}), flush=True)
        finally:
            compose('start', 'periplus-materializer')
        started = time.monotonic()
        deadline = started+120
        while time.monotonic() < deadline:
            response = api.get('/collections/'+identity)
            response.raise_for_status()
            if response.json()['query_ready']:
                break
            time.sleep(1)
        else:
            raise AssertionError('Queued capture did not become query-ready after worker restart')
        payload['sql'] = ("SELECT c.capture_id,e.text,l.target_url FROM public_v1.capture c "
            "JOIN public_v1.html_element e ON e.content_id=c.content_id "
            "JOIN public_v1.link l ON l.capture_id=c.capture_id "
            "WHERE c.capture_id=? AND e.tag='h1'")
        response = query.post('/query/exec', json=payload)
        response.raise_for_status()
        result = response.json()
        assert result['rows'] == [[visit, 'Example Domain', 'https://iana.org/domains/example']], result
        assert not result['truncated'], result
        print(json.dumps({'stage': 'recovered', 'collection_id': identity, 'capture_id': visit,
                          'query_id': result['query_id'], 'recovery_observed_ms':
                          round((time.monotonic()-started)*1000, 2), 'rows': result['rows']}))


if __name__ == '__main__':
    main()
