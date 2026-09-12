"""Check whether alternate SQL avoids reading option 1's nested node lists."""
import argparse
import json
import statistics
import time
from pathlib import Path

from compare import attach, connect, literal, query_sql
from periplus.query.benchmarking import bounded_rows, deadline, result_digest

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
root = p.parse_args().root.resolve()
selected = json.loads((root/'icu-source/source.json').read_text())['selected']
records = []
for folder_name in ('icu-term-b8', 'icu-term-b8-r2048', 'icu-term-b8-x4-n40'):
    folder = root/folder_name
    for name in ('rare', 'common'):
        term = selected[name]
        variants = {
            'recursive': query_sql('term',[term],'contents',[]),
            'lambda': f'SELECT DISTINCT unnest(list_transform(postings,p->p.content_id)) AS content_id FROM lake.posting WHERE term={literal(term)} ORDER BY content_id',
            'field_after_unnest': f'SELECT DISTINCT (unnest(postings)).content_id AS content_id FROM lake.posting WHERE term={literal(term)} ORDER BY content_id',
        }
        expected = None
        for variant, sql in variants.items():
            c = connect('512MB')
            attach(c,folder,read_only=True)
            with deadline(c,120):
                times = []
                for _ in range(3):
                    start = time.perf_counter()
                    rows = bounded_rows(c.execute(sql))
                    times.append((time.perf_counter()-start)*1000)
                    digest = result_digest(rows,ordered=True)
                    if expected is not None:
                        assert digest == expected
                    expected = digest
                profile = json.loads(c.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+sql).fetchone()[1])
            scans = []
            def visit(node):
                if 'SCAN' in node.get('operator_name',''):
                    scans.append(node.get('extra_info'))
                for child in node.get('children',[]):
                    visit(child)
            visit(profile)
            records.append({'layout':folder_name,'query':name,'variant':variant,'median_warm_ms':statistics.median(times[1:]),'equal':True,'scans':scans})
            c.close()
(root/'projection-probe.json').write_text(json.dumps(records,indent=2)+'\n')
print(json.dumps(records),flush=True)
