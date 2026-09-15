"""Exercise the real control, crawl and public query services for one fresh page.

Run after setup and starting API, query, crawler, ingestor and materializer.
Credentials come from the normal Periplus environment and are never printed.
"""
import argparse
import json
import time

import httpx
from periplus.platform.config import get_str


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-url', default='http://127.0.0.1:8000')
    parser.add_argument('--query-url', default='http://127.0.0.1:8001')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    started = time.monotonic()
    with httpx.Client(base_url=args.api_url, timeout=15, trust_env=False,
                     headers={'Authorization': 'Bearer '+get_str('PERIPLUS_ADMIN_API_TOKEN')}) as api:
        response = api.post('/collections', json={'specification': {
            'seed_urls': ['https://example.com/'], 'page_limit': 1, 'max_depth': 0,
            'result_max_age_seconds': 0, 'max_duration_seconds': 120,
        }})
        response.raise_for_status()
        identity = response.json()['id']
        while time.monotonic()-started < args.timeout:
            response = api.get('/collections/'+identity)
            response.raise_for_status()
            collection = response.json()
            if collection.get('query_ready'):
                break
            time.sleep(1)
        else:
            raise RuntimeError(f'Collection {identity} did not become query-ready within the observation window')
        if (collection['consumed_pages'],collection['supplied_pages'],collection['ingested_pages']) != (1,1,1):
            raise AssertionError('Unexpected collection accounting')
        ready_ms = (time.monotonic()-started)*1000
        response = api.get('/collections/'+identity+'/arrivals')
        response.raise_for_status()
        arrivals = response.json()['items']
        if len(arrivals)!=1 or arrivals[0]['mode']=='reused':
            raise AssertionError('Smoke run did not produce one fresh capture')
        visit = arrivals[0]['observation_id']
    with httpx.Client(base_url=args.query_url, timeout=30, trust_env=False,
                     headers={'Authorization': 'Bearer '+get_str('PERIPLUS_QUERY_API_TOKEN')}) as query:
        response = query.post('/query/exec', json={
            'sql': "SELECT c.capture_id AS capture_id,e.text,l.target_url FROM public_v1.capture c "
                   "JOIN public_v1.html_element e ON e.content_id=c.content_id "
                   "JOIN public_v1.link l ON l.capture_id=c.capture_id WHERE c.capture_id=? AND e.tag='h1'",
            'parameters': [visit],
        })
        response.raise_for_status()
        result=response.json()
        if result['rows'] != [[visit,'Example Domain','https://iana.org/domains/example']] or result['truncated']:
            raise AssertionError('Public query did not return the expected complete capture')
    print(json.dumps({'collection_id': identity,'capture_id': visit,'query_id': result['query_id'],
                      'ready_observed_ms': round(ready_ms,2),'rows': result['rows']}))


if __name__=='__main__':
    main()
