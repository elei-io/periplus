"""Count actual Parquet HTTP response bytes using an ephemeral loopback server.

This is a byte/request probe, not a simulated S3 latency benchmark. Native DuckLake
read-only DATA_PATH override preserves the same files, layout, and snapshot.
"""
import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from compare import connect, literal, query_sql
from periplus.query.benchmarking import bounded_rows, deadline, result_digest

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--variant')
args = p.parse_args()
root = args.root.resolve()


for folder in sorted(root.glob(args.variant or 'icu-*')):
    if not (folder / 'queries-maintained-0.json').exists() or (folder / 'http-reads.json').exists():
        continue
    build = json.loads((folder / 'build.json').read_text())
    selected = json.loads((Path(build['source']) / 'source.json').read_text())['selected']
    data = (folder / 'data').resolve()
    requests = []
    lock = threading.Condition()
    active = [0]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def do_HEAD(self):
            self.tracked(False)

        def do_GET(self):
            self.tracked(True)

        def tracked(self, body):
            with lock:
                active[0] += 1
            try:
                self.respond(body)
            finally:
                with lock:
                    active[0] -= 1
                    lock.notify_all()

        def respond(self, body):
            path = (data / unquote(urlsplit(self.path).path).lstrip('/')).resolve()
            if not path.is_relative_to(data) or not path.is_file():
                self.send_error(404)
                return
            size = path.stat().st_size
            lo, hi = 0, size - 1
            requested_range = self.headers.get('Range')
            if requested_range:
                left, right = requested_range.removeprefix('bytes=').split('-', 1)
                lo = int(left) if left else max(0, size - int(right))
                hi = min(size - 1, int(right)) if right and left else size - 1
            self.send_response(206 if requested_range else 200)
            self.send_header('Content-Length', str(hi - lo + 1))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Type', 'application/octet-stream')
            if requested_range:
                self.send_header('Content-Range', f'bytes {lo}-{hi}/{size}')
            self.end_headers()
            if body:
                with path.open('rb') as file:
                    file.seek(lo)
                    payload = file.read(hi - lo + 1)
                self.wfile.write(payload)
                self.wfile.flush()
            with lock:
                requests.append({'file': str(path.relative_to(data)), 'method': self.command, 'bytes': hi - lo + 1 if body else 0})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    records = []
    try:
        for state in ('fresh', 'maintained'):
            metadata = folder / ('fresh-metadata.duckdb' if state == 'fresh' else 'metadata.duckdb')
            if not metadata.exists():
                continue
            query_path = folder / f'queries-{state}-0.json'
            if not query_path.exists():
                continue
            original = {r['name']: r for r in json.loads(query_path.read_text())}
            specs = [('rare', [selected['rare']], 'contents'), ('common_nodes', [selected['common']], 'nodes'), ('scoped_common', [selected['common']], 'scope')]
            for name, terms, kind in specs:
                if not original[name]['complete']:
                    continue
                snapshot = original[name]['snapshot']
                sql = query_sql(build['shape'], terms, kind, selected['scope'])
                c = connect('512MB')
                try:
                    c.execute('LOAD httpfs')
                    c.execute(f"ATTACH {literal('ducklake:' + str(metadata))} AS lake (READ_ONLY, DATA_PATH 'http://127.0.0.1:{server.server_port}/', OVERRIDE_DATA_PATH true)")
                    assert c.execute("SELECT id FROM ducklake_current_snapshot('lake')").fetchone()[0] == snapshot
                    with lock:
                        requests.clear()
                    with deadline(c, 120):
                        rows = bounded_rows(c.execute(sql), max_rows=2_000_000, max_bytes=256 * 1024 * 1024)
                    assert result_digest(rows, ordered=True) == original[name]['digest']
                    c.close()
                    with lock:
                        assert lock.wait_for(lambda: active[0] == 0, timeout=10)
                        observed = list(requests)
                    assert observed, 'Data-path override did not produce HTTP reads'
                    records.append({'state': state, 'name': name, 'snapshot': snapshot, 'rows': len(rows), 'digest_equal': True, 'get_requests': sum(r['method'] == 'GET' for r in observed), 'head_requests': sum(r['method'] == 'HEAD' for r in observed), 'response_body_bytes': sum(r['bytes'] for r in observed), 'files_requested': len({r['file'] for r in observed})})
                finally:
                    c.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    (folder / 'http-reads.json').write_text(json.dumps(records, indent=2) + '\n')
    print(json.dumps({'variant': folder.name, 'http_reads': records}), flush=True)
