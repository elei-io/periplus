"""Read-only, bounded production evidence inventory. No credentials in output.

Run via homelab make run, so its supported operator credential lifecycle is used.
Executes a separate one-thread, 512 MB DuckDB process in the existing query pod,
using its SELECT/GetObject-only role. Loads already installed extensions only.
Does not call application mutations, modify settings, or read raw HTML remotely.
"""

import argparse
import json
import subprocess
from pathlib import Path

REMOTE = r'''
import json, time, threading, os
from datetime import datetime, UTC
import duckdb
from periplus.platform.catalogue.config import catalogue_config_from_env
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory

hard = threading.Timer(45, lambda: os._exit(124)); hard.start()
c = duckdb.connect(':memory:', config={'threads': '1', 'memory_limit': '512MB', 'max_temp_directory_size': '0B'})
timer = threading.Timer(20, c.interrupt)
started = time.perf_counter()
try:
 config = catalogue_config_from_env()
 factory = DuckLakeConnectionFactory(config)
 c.execute('LOAD ducklake; LOAD postgres; LOAD httpfs;')
 for sql in factory.storage.cli_init_sql():
  # All credentials remain inside this process. Never print statements/errors.
  for stmt in sql.split(';'):
   if stmt.strip() and not stmt.strip().upper().startswith('INSTALL '): c.execute(stmt)
 c.execute(factory.attach_sql(read_only=True))
 c.execute('USE "'+config.alias+'"')
 c.execute('BEGIN TRANSACTION')
 timer.start()
 sql = """
 WITH grouped AS (
  SELECT d.content_sha256,
    lower(d.detected_media_type)='text/html' AS html,
    count(*) AS n,
    count(DISTINCT v.requested_url) AS urls,
    min(d.content_bytes) AS min_logical, max(d.content_bytes) AS logical,
    min(d.stored_bytes) AS min_stored, max(d.stored_bytes) AS stored,
    sum(d.content_bytes) AS repeated_logical,
    sum(d.stored_bytes) AS repeated_stored
  FROM ingest.documents d JOIN ingest.visits v USING(visit_id)
  WHERE d.content_sha256 IS NOT NULL
  GROUP BY d.content_sha256, html
 )
 SELECT html, sum(n) AS captures, count(*) AS distinct_contents,
   sum(repeated_logical) AS bytes_without_dedupe,
   sum(logical) AS bytes_with_dedupe,
   sum(repeated_stored) AS compressed_without_dedupe,
   sum(stored) AS compressed_with_dedupe,
   sum(n-urls) AS repeats_within_same_requested_url,
   sum(urls-1) AS extra_requested_urls_sharing_content,
   count(*) FILTER (WHERE n>1) AS repeated_contents,
   count(*) FILTER (WHERE urls>1) AS contents_shared_across_urls,
   count(*) FILTER (WHERE min_logical<>logical) AS logical_size_conflicts,
   count(*) FILTER (WHERE min_stored<>stored) AS stored_size_variants,
   sum((n-urls)*stored) AS within_url_compressed_savings_estimate,
   sum((urls-1)*stored) AS cross_url_compressed_savings_estimate
 FROM grouped GROUP BY html ORDER BY html DESC
 """
 result=c.execute(sql)
 columns=[x[0] for x in result.description]
 rows=[dict(zip(columns,row)) for row in result.fetchall()]
 print(json.dumps({'observed_at':datetime.now(UTC).isoformat(), 'seconds':time.perf_counter()-started,
  'read_only':True,'threads':1,'memory_limit':'512MB','query_deadline_seconds':20,
  'columns':columns,'rows':rows},default=str),flush=True)
except Exception as exc:
 print(json.dumps({'error':type(exc).__name__,'seconds':time.perf_counter()-started}),flush=True)
finally:
 timer.cancel(); c.close(); hard.cancel()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            "applications",
            "exec",
            "-i",
            "deployment/periplus-query",
            "--",
            "python",
            "-",
        ],
        input=REMOTE,
        text=True,
        capture_output=True,
        timeout=55,
    )
    records = []
    for line in result.stdout.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            pass
    if result.returncode:
        records.append(
            {"error": "reader_probe_failed", "returncode": result.returncode}
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
