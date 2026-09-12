"""Native-only compaction inside the retained production maintenance image."""
import json
import sys
import time
from pathlib import Path

import duckdb

root = Path(sys.argv[1])
c = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
c.execute("LOAD '/opt/lakeducktor/ducklake.duckdb_extension'")
c.execute("SET threads=2")
c.execute("SET memory_limit='4GB'")
c.execute("SET max_temp_directory_size='8GB'")
path = str(root).replace("'", "''")
c.execute(f"SET temp_directory='{path}/spill'")
c.execute(f"ATTACH 'ducklake:{path}/metadata.duckdb' AS lake (DATA_PATH '{path}/data')")
count = c.execute('SELECT count(*) FROM lake.posting').fetchone()[0]
start = time.perf_counter()
merges = c.execute("CALL ducklake_merge_adjacent_files('lake','posting')").fetchall()
elapsed = time.perf_counter() - start
assert c.execute('SELECT count(*) FROM lake.posting').fetchone()[0] == count
stats = c.execute('SELECT count(*),sum(file_size_bytes),sum(record_count) FROM __ducklake_metadata_lake.main.ducklake_data_file WHERE end_snapshot IS NULL').fetchone()
report = {'seconds': elapsed, 'merges': merges, 'files': stats[0], 'bytes': stats[1], 'rows': stats[2], 'engine': duckdb.__version__, 'memory_limit': '4GB', 'extension': c.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='ducklake'").fetchone()[0]}
c.close()
(root / 'compaction.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
