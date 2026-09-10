"""Compare one native optimizer alternative on a disposable read-only connection.

Use configured reader credentials. This does not change QueryService settings or
choose a production execution policy. Output contains counts, timings and plan
findings, never credentials, URL parameters, SQL, or result rows.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import json
import threading
import time

from periplus.platform.catalogue.config import catalogue_config_from_env
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
from periplus.query.content_scope import content_scope
from periplus.query.scope_plan import shared_html_inputs


@contextmanager
def deadline(connection, seconds):
    timer = threading.Timer(seconds, connection.interrupt)
    timer.start()
    try:
        yield
    finally:
        timer.cancel()
        timer.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, choices=range(1, 61), default=20)
    parser.add_argument('--repetitions', type=int, choices=range(1, 4), default=2)
    parser.add_argument('--case', action='append', choices=('one_url', 'gov_phrase', 'full', 'empty'))
    args = parser.parse_args()
    config = catalogue_config_from_env()
    connection = DuckLakeConnectionFactory(config, duckdb_config={
        'threads': '2', 'memory_limit': '512MB', 'max_temp_directory_size': '256MB',
    }).connect(read_only=True)
    try:
        connection.execute(f'USE "{config.alias}".public_v1')
        with deadline(connection, args.seconds):
            url = connection.execute("SELECT effective_url FROM capture WHERE effective_url LIKE '%.gov%' ORDER BY content_id LIMIT 1").fetchone()
        if url is None:
            raise ValueError('No representative government capture available')
        base = '''SELECT c.effective_url, c.captured_at, h.text AS heading, s.*
            FROM capture c JOIN html_heading h USING(content_id) JOIN html_section s
            ON s.content_id=h.content_id AND s.heading_node_index=h.node_index WHERE '''
        cases = {
            'one_url': (base + 'c.effective_url=?', [url[0]]),
            'gov_phrase': (base + "c.effective_url LIKE ? AND lower(h.text) LIKE '%artificial intelligence%' ORDER BY c.captured_at DESC", ['%.gov%']),
            'full': (base + 'c.effective_url LIKE ?', ['%']),
            'empty': (base + 'c.effective_url=?', ['https://absent.invalid/periplus-scope-probe']),
        }
        baseline_setting = connection.execute("SELECT current_setting('disabled_optimizers')").fetchone()[0]
        alternative = ','.join(filter(None, (baseline_setting, 'common_subplan')))
        for name in args.case or cases:
            sql, parameters = cases[name]
            scoped = content_scope(sql)
            assert scoped is not None
            for repetition in range(args.repetitions):
                # Both successful variants share a snapshot. Stop a pair after
                # interruption; never compare partial results or cross snapshots.
                connection.execute('BEGIN')
                try:
                    with deadline(connection, args.seconds):
                        snapshot = connection.execute('SELECT id FROM ducklake_current_snapshot(?)', [config.alias]).fetchone()[0]
                        installed = dict(connection.execute("SELECT view_name,sql FROM duckdb_views() WHERE database_name=? AND schema_name='public_v1'", [config.alias]).fetchall())
                        assert scoped.matches(connection, installed)
                    record = dict(case=name, repetition=repetition, snapshot=snapshot, variants=[])
                    results = []
                    variants = [('default', baseline_setting), ('without_common_subplan', alternative)]
                    if repetition % 2 == 0:
                        variants.reverse()
                    for label, setting in variants:
                        connection.execute('SET disabled_optimizers=?', [setting])
                        started = time.monotonic()
                        item = dict(variant=label)
                        try:
                            with deadline(connection, args.seconds):
                                raw = connection.execute('EXPLAIN (FORMAT JSON) ' + scoped.sql, parameters).fetchone()[-1]
                                item['shared_html_inputs'] = shared_html_inputs(raw, key_cte=scoped.key_cte)
                                item['prepare_seconds'] = round(time.monotonic() - started, 4)
                                started = time.monotonic()
                                cursor = connection.execute(scoped.sql, parameters)
                                description = cursor.description
                                rows = Counter()
                                row_count = size = 0
                                while (row := cursor.fetchone()) is not None:
                                    encoded = repr(row)
                                    size += len(encoded.encode())
                                    row_count += 1
                                    if row_count > 100_000 or size > 32 * 1024 * 1024:
                                        raise ValueError('comparison_result_bound')
                                    rows[encoded] += 1
                                item['rows'] = row_count
                                results.append((description, rows))
                        except Exception as exc:
                            # Native errors can contain credential-bearing URLs.
                            item['error_type'] = type(exc).__name__
                        item['seconds'] = round(time.monotonic() - started, 4)
                        record['variants'].append(item)
                        print(json.dumps(dict(case=name, repetition=repetition, snapshot=snapshot, **item)), flush=True)
                        if 'error_type' in item:
                            break
                    record['equivalent'] = results[0] == results[1] if len(results) == 2 else None
                    print(json.dumps(record), flush=True)
                    if record['equivalent'] is False:
                        raise AssertionError('Optimizer alternatives changed results')
                finally:
                    connection.execute('ROLLBACK')
        connection.execute('SET disabled_optimizers=?', [baseline_setting])
    finally:
        connection.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Attachment/cleanup errors can contain reader connection details too.
        print(json.dumps({'probe_error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
