"""Isolated append-only layout bake-off; uses the shared query bench primitives."""
from __future__ import annotations

import argparse
import json
import resource
import shutil
import statistics
import subprocess
import time
from pathlib import Path

import duckdb

from periplus.query.benchmarking import bounded_rows, deadline, inspect_profile, result_digest


def literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def connect(memory: str = "2GB"):
    c = duckdb.connect()
    c.execute("SET threads=2")
    c.execute(f"SET memory_limit={literal(memory)}")
    c.execute("SET max_temp_directory_size='8GB'")
    return c


def write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def page_words(text):
    """Segment original page text; normalize keys without changing source offsets."""
    import icu
    assert (icu.VERSION, icu.ICU_VERSION, icu.UNICODE_VERSION) == ('2.16.2', '77.1', '16.0')
    normalizer = icu.Normalizer2.getNFCInstance()
    original = icu.UnicodeString(text)
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(original)
    start_utf16 = iterator.first()
    start_cp = 0
    for end_utf16 in iterator:
        piece = original[start_utf16:end_utf16]
        end_cp = start_cp + len(str(piece))
        if iterator.getRuleStatus() >= 100:
            piece.foldCase()
            yield normalizer.normalize(piece), start_cp, end_cp
        start_utf16, start_cp = end_utf16, end_cp


def prepare(args):
    import pyarrow as pa
    assert list(page_words('catfish')) == [('catfish', 0, 7)]
    assert list(page_words('🐒 CAFÉ')) == [('café', 2, 6)]
    assert list(page_words('Straße')) == [('strasse', 0, 6)]
    assert list(page_words('cafe\u0301')) == [('café', 0, 5)]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    c = connect()
    c.execute(f"ATTACH {literal('ducklake:' + str(args.fixture.resolve()))} AS fixture (READ_ONLY, METADATA_SCHEMA 'ducklake')")
    c.execute(f"ATTACH {literal(out / 'source.duckdb')} AS source")
    c.execute("CREATE TABLE source.contents AS SELECT content_sha256 AS content_id, (row_number() OVER (ORDER BY md5(content_sha256))-1)::INTEGER AS ordinal FROM fixture.material.html_elements GROUP BY content_sha256")
    c.execute("CREATE TABLE source.matches(term VARCHAR, content_id VARCHAR, node_index INTEGER, ordinal INTEGER)")
    c.execute("CREATE TABLE source.elements AS SELECT e.content_sha256 AS content_id,e.node_index,e.text_start,e.text_end,ids.ordinal FROM fixture.material.html_elements e JOIN source.contents ids ON e.content_sha256=ids.content_id")
    counts = c.execute("SELECT count(*), count(DISTINCT content_sha256), sum(length(text)) FROM fixture.material.html_elements").fetchone()
    tokenizer = 'PyICU 2.16.2 / ICU 77.1 / Unicode 16.0; root word breaks on original full-page text; term keys case folded then NFC; original code-point positions'
    report = {"engine": duckdb.__version__, "fixture": str(args.fixture.resolve()), "snapshot": c.execute("SELECT id FROM ducklake_current_snapshot('fixture')").fetchone()[0], "elements": counts[0], "contents": counts[1], "element_text_characters": counts[2], "page_text_characters": c.execute('SELECT sum(length(text)) FROM fixture.material.html_elements WHERE parent_index IS NULL').fetchone()[0], "tokenizer": tokenizer, "mapping": "Every element fully containing at least one complete page-token occurrence; distinct term/content/element; no partial boundary words", "batches": [], "tokenization_batches": [], "mapping_batches": [], "occurrences": 0}
    schema = pa.schema([('content_id',pa.string()),('term',pa.string()),('word_start',pa.int64()),('word_end',pa.int64())])
    for offset in range(0, counts[1], 50):
        start = time.perf_counter()
        pages = c.execute("SELECT e.content_sha256,e.text,e.text_start FROM fixture.material.html_elements e JOIN source.contents ids ON e.content_sha256=ids.content_id WHERE e.parent_index IS NULL AND ids.ordinal>=? AND ids.ordinal<? ORDER BY ids.ordinal", [offset,offset+50]).fetchall()
        occurrences = [(content,term,base+lo,base+hi) for content,text,base in pages for term,lo,hi in page_words(text)]
        report['tokenization_batches'].append(time.perf_counter()-start)
        report['occurrences'] += len(occurrences)
        mapping_start = time.perf_counter()
        columns = zip(*occurrences, strict=True)
        table = pa.Table.from_arrays([pa.array(values,type=field.type) for values,field in zip(columns,schema,strict=True)],schema=schema)
        c.register('occurrences',table)
        with deadline(c, 120):
            c.execute("""INSERT INTO source.matches
                SELECT DISTINCT o.term,o.content_id,e.node_index,e.ordinal
                FROM occurrences o JOIN source.elements e ON o.content_id=e.content_id
                  AND e.text_start<=o.word_start AND o.word_end<=e.text_end""")
        c.unregister('occurrences')
        del occurrences, table
        report['mapping_batches'].append(time.perf_counter()-mapping_start)
        report['batches'].append(time.perf_counter() - start)
        print(json.dumps({"prepared_contents": min(offset + 50, counts[1]), "seconds": report['batches'][-1]}), flush=True)
    c.execute("CREATE TABLE source.stats AS SELECT term, count(*) AS nodes, count(DISTINCT content_id) AS contents FROM source.matches GROUP BY term")
    # Prefer an interpretable real word, but choose by observed selectivity when absent.
    rare = c.execute("SELECT term FROM source.stats WHERE contents BETWEEN 2 AND 5 AND regexp_full_match(term,'[a-z]{5,20}') ORDER BY CASE WHEN term='monkey' THEN 0 ELSE 1 END, term LIMIT 1").fetchone()[0]
    common = c.execute("SELECT term FROM source.stats WHERE contents>100 AND regexp_full_match(term,'[a-z]{2,20}') ORDER BY CASE WHEN term='the' THEN 0 ELSE 1 END,contents DESC,nodes DESC,term LIMIT 1").fetchone()[0]
    medium = c.execute("SELECT term FROM source.stats WHERE contents BETWEEN 20 AND 100 AND regexp_full_match(term,'[a-z]{4,20}') ORDER BY CASE WHEN term='pricing' THEN 0 ELSE 1 END,term LIMIT 1").fetchone()[0]
    scope = [r[0] for r in c.execute("SELECT DISTINCT content_id FROM source.matches WHERE term=? ORDER BY content_id", [rare]).fetchall()]
    assert c.execute("SELECT count(*) FROM source.stats WHERE term='zzzzabsentindexprobezzzz'").fetchone()[0] == 0
    report.update({"flat_rows": c.execute("SELECT count(*) FROM source.matches").fetchone()[0], "term_content_rows": c.execute("SELECT count(*) FROM (SELECT term,content_id FROM source.matches GROUP BY ALL)").fetchone()[0], "terms": c.execute("SELECT count(*) FROM source.stats").fetchone()[0], "selected": {"rare": rare, "common": common, "medium": medium, "absent": "zzzzabsentindexprobezzzz", "scope": scope}, "selected_stats": c.execute("SELECT * FROM source.stats WHERE term IN (?,?,?)", [rare, common, medium]).fetchall(), "tokenization_s": sum(report['tokenization_batches']), "mapping_s": sum(report['mapping_batches']), "preparation_s": sum(report['batches']), "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
    c.close()
    write_json(out / 'source.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('selected', 'fixture', 'batches')}), flush=True)


SHAPES = {
    'term': ("term VARCHAR, postings STRUCT(content_id VARCHAR, node_indexes INTEGER[])[]", "term"),
    'content': ("term VARCHAR, content_id VARCHAR, node_indexes INTEGER[]", "term, content_id"),
    'node': ("term VARCHAR, content_id VARCHAR, node_index INTEGER", "term, content_id, node_index"),
}


def attach(c, folder: Path, read_only=False):
    options = ', READ_ONLY' if read_only else ''
    c.execute(f"ATTACH {literal('ducklake:' + str(folder / 'metadata.duckdb'))} AS lake (DATA_PATH {literal(folder / 'data')}{options})")


def file_stats(c):
    return dict(zip(('files', 'bytes', 'rows'), c.execute("SELECT count(*), coalesce(sum(file_size_bytes),0), coalesce(sum(record_count),0) FROM __ducklake_metadata_lake.main.ducklake_data_file WHERE end_snapshot IS NULL").fetchone(), strict=True))


def assert_same_multiset(c, expected, actual, partitions=1):
    # Partition the verification work only; never change the measured table layout.
    for part in range(partitions):
        left = f'SELECT * FROM ({expected}) WHERE hash(content_id)%{partitions}={part}'
        right = f'SELECT * FROM ({actual}) WHERE hash(content_id)%{partitions}={part}'
        with deadline(c, 120):
            mismatch = c.execute(f'SELECT count(*) FROM (({left} EXCEPT ALL {right}) UNION ALL ({right} EXCEPT ALL {left}))').fetchone()[0]
        assert mismatch == 0


def build(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve()
    info = json.loads((source / 'source.json').read_text())
    c = connect()
    c.execute(f"ATTACH {literal(source / 'source.duckdb')} AS source (READ_ONLY)")
    attach(c, root)
    c.execute("CALL lake.set_option('data_inlining_row_limit',0)")
    c.execute("CALL lake.set_option('parquet_compression','zstd')")
    c.execute(f"CALL lake.set_option('parquet_row_group_size',{args.row_group_size})")
    c.execute(f"CALL lake.set_option('target_file_size',{literal(args.target_file_size)})")
    schema, sort = SHAPES[args.shape]
    c.execute(f"CREATE TABLE lake.posting ({schema})")
    if args.buckets:
        c.execute(f"ALTER TABLE lake.posting SET PARTITIONED BY (bucket({args.buckets},term))")
    c.execute(f"ALTER TABLE lake.posting SET SORTED BY ({sort})")
    total_contents = info['contents'] * args.copies
    report = {"shape": args.shape, "buckets": args.buckets, "batches": args.batches, "copies": args.copies, "contents": total_contents, "source": str(source), "settings": {"threads": 2, "build_memory": "2GB", "query_memory": "512MB", "row_group_size": args.row_group_size, "target_file_size": args.target_file_size, "compression": "zstd"}, "writes": [], "engine": duckdb.__version__}
    c.execute("CREATE TEMP TABLE corpus AS SELECT m.term, CASE WHEN r.i=0 THEN m.content_id ELSE sha256(m.content_id || ':' || r.i::VARCHAR) END AS content_id, m.node_index, (m.ordinal + r.i * ?) AS ordinal FROM source.matches m CROSS JOIN range(?) r(i) WHERE r.i=0 OR m.term<>?", [info['contents'], args.copies, info['selected']['rare']])
    report['flat_rows'] = c.execute('SELECT count(*) FROM corpus').fetchone()[0]
    for batch in range(args.batches):
        c.execute("CREATE OR REPLACE TEMP TABLE batch_flat AS SELECT term,content_id,node_index FROM corpus WHERE ordinal % ? = ?", [args.batches, batch])
        start = time.perf_counter()
        with deadline(c, 120):
            if args.shape == 'node':
                sql = "SELECT * FROM batch_flat"
            elif args.shape == 'content':
                sql = "SELECT term,content_id,list(node_index ORDER BY node_index) AS node_indexes FROM batch_flat GROUP BY term,content_id"
            else:
                sql = "SELECT term,list(struct_pack(content_id:=content_id,node_indexes:=node_indexes) ORDER BY content_id) AS postings FROM (SELECT term,content_id,list(node_index ORDER BY node_index) AS node_indexes FROM batch_flat GROUP BY term,content_id) GROUP BY term"
            c.execute(f"INSERT INTO lake.posting {sql}")
        report['writes'].append(time.perf_counter() - start)
    report['write_s'] = sum(report['writes'])
    report['fresh'] = file_stats(c)
    report['snapshot'] = c.execute("SELECT id FROM ducklake_current_snapshot('lake')").fetchone()[0]
    report['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Exact whole-index equivalence, including multiplicity, before query sampling.
    if args.shape == 'term':
        flat = "SELECT term, unnest(postings, recursive:=true) FROM lake.posting"
        flat = f"SELECT term,content_id,unnest(node_indexes) AS node_index FROM ({flat})"
    elif args.shape == 'content':
        flat = "SELECT term,content_id,unnest(node_indexes) AS node_index FROM lake.posting"
    else:
        flat = "SELECT * FROM lake.posting"
    assert_same_multiset(c, 'SELECT term,content_id,node_index FROM corpus', flat, 8 if args.copies > 1 else 1)
    report['whole_index_exact_equality'] = True
    c.close()
    # Time travel preserves logical data, not necessarily pre-compaction files.
    # Keep a closed metadata copy for exact fresh-layout HTTP read probes.
    shutil.copy2(root / 'metadata.duckdb', root / 'fresh-metadata.duckdb')
    write_json(root / 'build.json', report)
    print(json.dumps({k: v for k, v in report.items() if k not in ('source', 'writes')}), flush=True)


def query_sql(shape, terms, kind, scope):
    pred = 'term IN (' + ','.join(literal(t) for t in terms) + ')'
    if shape == 'term':
        base = f"SELECT term,unnest(postings,recursive:=true) FROM lake.posting WHERE {pred}"
    elif shape == 'content':
        base = f"SELECT * FROM lake.posting WHERE {pred}"
    else:
        base = f"SELECT * FROM lake.posting WHERE {pred}"
    if kind == 'scope':
        base = f"SELECT * FROM ({base}) WHERE content_id IN ({','.join(literal(x) for x in scope)})"
    if kind in ('nodes', 'scope'):
        flat = base if shape == 'node' else f"SELECT content_id,unnest(node_indexes) AS node_index FROM ({base})"
        return f"SELECT content_id,node_index FROM ({flat}) ORDER BY content_id,node_index"
    if kind == 'and':
        return f"SELECT content_id FROM ({base}) GROUP BY content_id HAVING count(DISTINCT term)={len(terms)} ORDER BY content_id"
    suffix = ' LIMIT 10' if kind == 'limit' else ''
    return f"SELECT DISTINCT content_id FROM ({base}) ORDER BY content_id{suffix}"


def queries(args):
    root = args.output.resolve()
    build_info = json.loads((root / 'build.json').read_text())
    source = Path(build_info['source'])
    info = json.loads((source / 'source.json').read_text())
    s = info['selected']
    specs = [('absent', [s['absent']], 'contents'), ('rare', [s['rare']], 'contents'), ('medium', [s['medium']], 'contents'), ('common', [s['common']], 'contents'), ('two_words', [s['rare'], s['common']], 'and'), ('rare_nodes', [s['rare']], 'nodes'), ('common_nodes', [s['common']], 'nodes'), ('scoped_common', [s['common']], 'scope'), ('common_limit', [s['common']], 'limit')]
    if args.reverse:
        specs.reverse()
    results = []
    for name, terms, kind in specs:
        c = connect('512MB')
        attach(c, root, read_only=True)
        sql = query_sql(build_info['shape'], terms, kind, s['scope'])
        item = {'name': name, 'state': args.state, 'reverse': args.reverse, 'snapshot': c.execute("SELECT id FROM ducklake_current_snapshot('lake')").fetchone()[0]}
        try:
            times = []
            with deadline(c, 120):
                for repeat in range(3):
                    start = time.perf_counter()
                    cursor = c.execute(sql)
                    description = [(col[0], str(col[1])) for col in cursor.description]
                    rows = bounded_rows(cursor, max_rows=2_000_000, max_bytes=256 * 1024 * 1024)
                    times.append((time.perf_counter() - start) * 1000)
                    digest = result_digest(rows, ordered=True)
                    if repeat:
                        assert digest == item['digest']
                    item.update({'rows': len(rows), 'digest': digest, 'columns': description})
                profile = json.loads(c.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql).fetchone()[1])
            blocking, scans = inspect_profile(profile)
            item.update({'complete': True, 'first_ms': times[0], 'warm_ms': times[1:], 'median_warm_ms': statistics.median(times[1:]), 'scans': scans, 'blocking': blocking, 'profile_bytes_read': profile.get('total_bytes_read'), 'profile_peak_buffer': profile.get('system_peak_buffer_memory'), 'profile_peak_temp': profile.get('system_peak_temp_dir_size'), 'rows_scanned': profile.get('cumulative_rows_scanned')})
            write_json(root / f'profile-{args.state}-{name}-{int(args.reverse)}.json', profile)
            # A separate fresh DuckDB connection measures engine-reported reads.
            # The OS/disk cache is not flushed: this is NOT a cold-storage test.
            c.close()
            c = connect('512MB')
            attach(c, root, read_only=True)
            with deadline(c, 120):
                fresh_profile = json.loads(c.execute('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql).fetchone()[1])
            item['fresh_connection_profile_bytes_read'] = fresh_profile.get('total_bytes_read')
            item['fresh_connection_profile_peak_buffer'] = fresh_profile.get('system_peak_buffer_memory')
            write_json(root / f'first-profile-{args.state}-{name}-{int(args.reverse)}.json', fresh_profile)
        except Exception as exc:
            item.update({'complete': False, 'error': type(exc).__name__})
        c.close()
        results.append(item)
    write_json(root / f'queries-{args.state}-{int(args.reverse)}.json', results)
    print(json.dumps({'directory': root.name, 'state': args.state, 'reverse': args.reverse, 'complete': all(x['complete'] for x in results), 'queries': [{k: v for k, v in r.items() if k in ('name', 'rows', 'median_warm_ms', 'error')} for r in results]}), flush=True)


def compact(args):
    root = args.output.resolve()
    script = Path(__file__).with_name('compact.py').resolve()
    cmd = ['docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none', '--cpus', '2', '--memory', '6g', '-v', f'{root}:{root}', '-v', f'{script}:/experiment.py:ro', '--entrypoint', 'python', args.image, '/experiment.py', str(root)]
    subprocess.run(cmd, check=True, timeout=600)
    # Confirm the complete multiset survives maintenance, not just sampled searches.
    info = json.loads((root / 'build.json').read_text())
    source_info = json.loads((Path(info['source']) / 'source.json').read_text())
    c = connect()
    attach(c, root, read_only=True)
    c.execute(f"ATTACH {literal(Path(info['source']) / 'source.duckdb')} AS source (READ_ONLY)")
    c.execute(f"CREATE TEMP TABLE identities AS SELECT m.content_id AS source_id,CASE WHEN r.i=0 THEN m.content_id ELSE sha256(m.content_id || ':' || r.i::VARCHAR) END AS content_id,r.i AS copy FROM source.contents m CROSS JOIN range({info['copies']}) r(i)")
    expected = f"SELECT m.term,ids.content_id,m.node_index FROM source.matches m JOIN identities ids ON m.content_id=ids.source_id WHERE ids.copy=0 OR m.term<>{literal(source_info['selected']['rare'])}"
    if info['shape'] == 'term':
        actual = "SELECT term,content_id,unnest(node_indexes) AS node_index FROM (SELECT term,unnest(postings,recursive:=true) FROM lake.posting)"
    elif info['shape'] == 'content':
        actual = "SELECT term,content_id,unnest(node_indexes) AS node_index FROM lake.posting"
    else:
        actual = "SELECT * FROM lake.posting"
    assert_same_multiset(c, expected, actual, 8 if info['copies'] > 1 else 1)
    c.close()
    report = json.loads((root / 'compaction.json').read_text())
    report['whole_index_exact_equality'] = True
    report['image'] = args.image
    write_json(root / 'compaction.json', report)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=('prepare', 'build', 'query', 'compact'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fixture', type=Path)
    p.add_argument('--source', type=Path)
    p.add_argument('--shape', choices=SHAPES, default='content')
    p.add_argument('--buckets', type=int, default=8)
    p.add_argument('--batches', type=int, default=10)
    p.add_argument('--copies', type=int, default=1)
    p.add_argument('--row-group-size', type=int, default=122880)
    p.add_argument('--target-file-size', default='64MB')
    p.add_argument('--state', default='fresh')
    p.add_argument('--reverse', action='store_true')
    p.add_argument('--image', default='ghcr.io/elei-io/lake-ducktor@sha256:4ed6020562d906d1fb679c2125b776c0bfa378fadd8890310b892e27110a0841')
    args = p.parse_args()
    {'prepare': prepare, 'build': build, 'query': queries, 'compact': compact}[args.action](args)


if __name__ == '__main__':
    main()
