"""Compare batch-local mapping algorithms with exact option-2 output equality."""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
from pathlib import Path
import resource
import time

import pyarrow as pa

from compare import connect, deadline, literal, page_words, write_json


def map_page(words, elements):
    """Non-overlapping page tokens; elements may overlap or share endpoints."""
    starts = [word[1] for word in words]
    ends = [word[2] for word in words]
    terms = [word[0] for word in words]
    postings = defaultdict(list)
    for node, lo, hi in elements:
        first = bisect_left(starts, lo)
        last = bisect_right(ends, hi)
        for term in set(terms[first:last]):
            postings[term].append(node)
    return [(term, sorted(nodes)) for term, nodes in postings.items()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('baseline', 'scoped', 'integer', 'bisect'), required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    info = json.loads((args.source / 'source.json').read_text())
    c = connect()
    c.execute(f"ATTACH {literal(args.source / 'source.duckdb')} AS source (READ_ONLY)")
    c.execute(f"ATTACH {literal('ducklake:' + info['fixture'])} AS fixture (READ_ONLY, METADATA_SCHEMA 'ducklake')")
    assert c.execute("SELECT id FROM ducklake_current_snapshot('fixture')").fetchone()[0] == info['snapshot']
    schema = pa.schema([('term', pa.string()), ('content_id', pa.string()), ('node_indexes', pa.list_(pa.int32()))])
    report = {'mode': args.mode, 'engine': c.execute('SELECT version()').fetchone()[0], 'batches': []}
    for offset in range(0, info['contents'], 50):
        row = {'offset': offset}
        start = time.perf_counter()
        pages = c.execute('SELECT e.content_sha256,e.text,e.text_start,ids.ordinal FROM fixture.material.html_elements e JOIN source.contents ids ON e.content_sha256=ids.content_id WHERE e.parent_index IS NULL AND ids.ordinal>=? AND ids.ordinal<? ORDER BY ids.ordinal', [offset, offset+50]).fetchall()
        elements = c.execute('SELECT content_id,node_index,text_start,text_end,ordinal FROM source.elements WHERE ordinal>=? AND ordinal<? ORDER BY ordinal,node_index', [offset,offset+50]).fetchall()
        row['read_s'] = time.perf_counter()-start
        start = time.perf_counter()
        tokens = [(content, ordinal, [(term, base+lo, base+hi) for term,lo,hi in page_words(text)]) for content,text,base,ordinal in pages]
        row['tokenize_s'] = time.perf_counter()-start
        start = time.perf_counter()
        if args.mode == 'bisect':
            by_content = defaultdict(list)
            for content,node,lo,hi,_ in elements:
                by_content[content].append((node,lo,hi))
            packed = [(term,content,nodes) for content,_,words in tokens for term,nodes in map_page(words,by_content[content])]
            row['map_s'] = time.perf_counter()-start
            start = time.perf_counter()
            table = pa.Table.from_arrays([pa.array([r[i] for r in packed],type=f.type) for i,f in enumerate(schema)],schema=schema)
            c.register('packed',table)
            c.execute('CREATE OR REPLACE TEMP TABLE result AS SELECT * FROM packed')
            c.unregister('packed')
            row['arrow_and_output_s'] = time.perf_counter()-start
            del packed,table
        else:
            integer = args.mode == 'integer'
            key = 'ordinal' if integer else 'content_id'
            occurrences = [(ordinal if integer else content,term,lo,hi) for content,ordinal,words in tokens for term,lo,hi in words]
            occurrence_schema = pa.schema([(key,pa.int32() if integer else pa.string()),('term',pa.string()),('word_start',pa.int64()),('word_end',pa.int64())])
            table = pa.Table.from_arrays([pa.array(values,type=f.type) for values,f in zip(zip(*occurrences),occurrence_schema,strict=True)],schema=occurrence_schema)
            c.register('occurrences',table)
            row['arrow_s'] = time.perf_counter()-start
            start = time.perf_counter()
            select = f'SELECT DISTINCT o.term,e.content_id,e.node_index FROM occurrences o JOIN source.elements e ON o.{key}=e.{key} AND e.text_start<=o.word_start AND o.word_end<=e.text_end'
            if args.mode in ('scoped', 'integer'):
                select += f' WHERE e.ordinal>={offset} AND e.ordinal<{offset+50}'
            with deadline(c,120):
                c.execute('CREATE OR REPLACE TEMP TABLE flat AS '+select)
            row['join_dedupe_s'] = time.perf_counter()-start
            start = time.perf_counter()
            c.execute('CREATE OR REPLACE TEMP TABLE result AS SELECT term,content_id,list(node_index ORDER BY node_index)::INTEGER[] AS node_indexes FROM flat GROUP BY term,content_id')
            row['pack_s'] = time.perf_counter()-start
            if offset == 0:
                with deadline(c,120):
                    plan = c.execute('EXPLAIN (ANALYZE,FORMAT JSON) '+select).fetchone()[1]
                (args.output/'plan.json').write_text(plan)
            c.unregister('occurrences')
            del table,occurrences
        row['map_total_s'] = sum(row.get(k,0) for k in ('map_s','arrow_and_output_s','arrow_s','join_dedupe_s','pack_s'))
        # Exact arrays, multiplicities and identities, outside timing; bounded batch.
        expected = f'SELECT term,content_id,list(node_index ORDER BY node_index)::INTEGER[] AS node_indexes FROM source.matches WHERE ordinal>={offset} AND ordinal<{offset+50} GROUP BY term,content_id'
        with deadline(c,120):
            assert c.execute(f'SELECT count(*) FROM ((SELECT * FROM result EXCEPT ALL ({expected})) UNION ALL (({expected}) EXCEPT ALL SELECT * FROM result))').fetchone()[0] == 0
        row['rows'],row['nodes'] = c.execute('SELECT count(*),sum(len(node_indexes)) FROM result').fetchone()
        report['batches'].append(row)
        print(json.dumps(row),flush=True)
    report['totals'] = {k:sum(b.get(k,0) for b in report['batches']) for k in report['batches'][0] if k != 'offset'}
    report['exact_equal'] = True
    report['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    write_json(args.output/'report.json',report)
    c.close()


if __name__ == '__main__':
    main()
