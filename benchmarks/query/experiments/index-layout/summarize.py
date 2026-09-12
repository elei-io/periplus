"""Check result equality and produce a compact, sanitized measurement scorecard."""
import argparse
import json
import statistics
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
args = p.parse_args()
root = args.root.resolve()
rows = []
equivalence = {}
for folder in sorted(root.glob('icu-*')):
    if not (folder / 'build.json').exists():
        continue
    build = json.loads((folder / 'build.json').read_text())
    assert build['whole_index_exact_equality']
    maintained = json.loads((folder / 'compaction.json').read_text()) if (folder / 'compaction.json').exists() else None
    record = {'name': folder.name, 'shape': build['shape'], 'buckets': build['buckets'], 'batches': build['batches'], 'contents': build['contents'], 'copies': build['copies'], 'row_group_size': build['settings']['row_group_size'], 'write_s': build['write_s'], 'write_peak_rss_bytes': build['peak_rss_bytes'], 'fresh': build['fresh'], 'maintained': maintained, 'queries': {}}
    record['target_file_size'] = build['settings']['target_file_size']
    record['http_reads'] = json.loads((folder / 'http-reads.json').read_text()) if (folder / 'http-reads.json').exists() else []
    if maintained:
        assert maintained['whole_index_exact_equality']
    for path in sorted(folder.glob('queries-*.json')):
        for query in json.loads(path.read_text()):
            if not query['complete']:
                raise AssertionError((folder.name, query['name'], query['error']))
            key = (build['copies'], query['name'])
            answer = (query['digest'], query['rows'], query['columns'])
            if key in equivalence:
                assert equivalence[key] == answer, (key, folder.name)
            equivalence[key] = answer
            name = query['state'] + ':' + query['name']
            dest = record['queries'].setdefault(name, {'rows': query['rows'], 'warm_ms': [], 'first_ms': [], 'files_read': [], 'fresh_connection_bytes': [], 'profile_peak_buffer': []})
            dest['warm_ms'].extend(query['warm_ms'])
            dest['first_ms'].append(query['first_ms'])
            dest['files_read'].append(sum(int(s['files_read']) for s in query['scans'] if s['files_read'] is not None))
            dest['fresh_connection_bytes'].append(query.get('fresh_connection_profile_bytes_read'))
            dest['profile_peak_buffer'].append(query.get('fresh_connection_profile_peak_buffer'))
    for result in record['queries'].values():
        result['median_warm_ms'] = statistics.median(result['warm_ms'])
    rows.append(record)
(root / 'scorecard.json').write_text(json.dumps({'all_complete_query_results_equal': True, 'variants': rows}, indent=2) + '\n')
print('| Variant | Write s | Fresh MB | Maintained MB | Files fresh→maintained | Rare ms fresh→maintained | Rare files fresh→maintained | Maintained rare HTTP KB |')
print('|---|---:|---:|---:|---|---|---|---:|')
for r in rows:
    f = r['queries'].get('fresh:rare', {})
    m = r['queries'].get('maintained:rare', {})
    maintained = r['maintained'] or {}
    body_bytes = next((x['response_body_bytes'] for x in r['http_reads'] if x['state']=='maintained' and x['name']=='rare'), None)
    http_kb = f'{body_bytes/1000:.1f}' if body_bytes is not None else 'unknown'
    print(f"| {r['name']} | {r['write_s']:.2f} | {r['fresh']['bytes']/1e6:.2f} | {maintained.get('bytes',0)/1e6:.2f} | {r['fresh']['files']} → {maintained.get('files','failed')} | {f.get('median_warm_ms',0):.2f} → {m.get('median_warm_ms',0):.2f} | {f.get('files_read',[])} → {m.get('files_read',[])} | {http_kb} |")
print('All complete query result types, counts, and ordered digests agree across layouts and states.')
