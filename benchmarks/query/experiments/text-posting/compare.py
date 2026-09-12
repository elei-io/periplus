"""Disposable key/layout comparison using the shared query measurement primitives."""
import argparse
import json
import time
from pathlib import Path

import duckdb
import pyarrow as pa
from periplus.query.benchmarking import bounded_rows, deadline, inspect_profile, result_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    db = duckdb.connect()
    db.execute("SET threads=2; SET memory_limit='512MB'")
    for name in ('term', 'posting'):
        with pa.ipc.open_file(str(args.fixture / (name + '.arrow'))) as reader:
            db.register(name, reader.read_all())
    db.execute('CREATE TABLE source AS SELECT t.text,p.* FROM posting p JOIN term t USING(term_id)')
    # Preserve identical payloads and content sets; simulate independent appends
    # with disjoint synthetic content identities, not new language diversity.
    cases = {'numeric':'term_id', 'hash':'md5(text)::UUID', 'text':'text'}
    records = []
    terms = [r[0] for r in db.execute("SELECT text FROM source GROUP BY text HAVING count(*) BETWEEN 2 AND 5 ORDER BY text LIMIT 2").fetchall()]
    for mode, expression in cases.items():
        folder = args.output / mode
        folder.mkdir(exist_ok=True)
        for batch in range(10):
            path = folder / f'{batch}.parquet'
            started = time.perf_counter()
            db.execute(f"COPY (SELECT {expression} AS key, content_sha256 || '-{batch}' AS content_sha256,frequency,positions,node_indexes FROM source ORDER BY key,content_sha256) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)", [str(path)])
            records.append({'mode':mode,'batch':batch,'write_seconds':time.perf_counter()-started,'bytes':path.stat().st_size})
            if batch not in (0, 4, 9):
                continue
            for count in (1, 2):
                selected = terms[:count]
                if mode == 'numeric':
                    keys = [r[0] for r in db.execute('SELECT term_id FROM term WHERE text IN (SELECT unnest(?))', [selected]).fetchall()]
                elif mode == 'hash':
                    keys = [r[0] for r in db.execute('SELECT md5(unnest(?))::UUID', [selected]).fetchall()]
                else:
                    keys = selected
                literals = ','.join(str(k) if mode == 'numeric' else "'" + str(k).replace("'", "''") + "'" + ('::UUID' if mode == 'hash' else '') for k in keys)
                sql = f'SELECT content_sha256,sum(frequency)::BIGINT FROM read_parquet(?) WHERE key IN ({literals}) GROUP BY content_sha256 HAVING count(*)=? ORDER BY content_sha256'
                params = [str(folder/'*.parquet'), count]
                with deadline(db, 60):
                    start = time.perf_counter()
                    rows = bounded_rows(db.execute(sql, params))
                    elapsed = time.perf_counter()-start
                    profile = json.loads(db.execute('EXPLAIN (ANALYZE, FORMAT JSON) '+sql, params).fetchone()[1])
                _, scans = inspect_profile(profile)
                records.append({'mode':mode,'appends':batch+1,'terms':count,'seconds':elapsed,'digest':result_digest(rows,ordered=True),'rows':len(rows),'scans':scans,'bytes_read':profile.get('total_bytes_read'),'rows_scanned':profile.get('cumulative_rows_scanned')})
    for appends in (1,5,10):
        for count in (1,2):
            assert len({r['digest'] for r in records if r.get('appends')==appends and r.get('terms')==count})==1
    (args.output/'results.json').write_text(json.dumps(records,indent=2,default=str))
    print(json.dumps({'engine':duckdb.__version__,'posting_rows':db.execute('SELECT count(*) FROM source').fetchone()[0],'results':str(args.output/'results.json')}))


if __name__ == '__main__':
    main()
