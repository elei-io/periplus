"""Check whether further native merge passes eliminate file overlap."""
import json
import sys
import time
from pathlib import Path

import duckdb

root = Path(sys.argv[1])
path = str(root).replace("'", "''")
c = duckdb.connect(config={'allow_unsigned_extensions': 'true'})
c.execute("LOAD '/opt/lakeducktor/ducklake.duckdb_extension'")
c.execute("SET threads=2")
c.execute("SET memory_limit='4GB'")
c.execute("SET max_temp_directory_size='8GB'")
c.execute(f"SET temp_directory='{path}/spill'")
c.execute(f"ATTACH 'ducklake:{path}/metadata.duckdb' AS lake (DATA_PATH '{path}/data')")
before = c.execute("SELECT id FROM ducklake_current_snapshot('lake')").fetchone()[0]
count = c.execute('SELECT count(*) FROM lake.posting').fetchone()[0]
passes = []
for _ in range(10):
    start = time.perf_counter()
    merges = c.execute("CALL ducklake_merge_adjacent_files('lake','posting')").fetchall()
    passes.append({'seconds': time.perf_counter()-start, 'merges': merges})
    if not merges:
        break
else:
    raise RuntimeError('Native maintenance still has work after ten passes')
assert c.execute('SELECT count(*) FROM lake.posting').fetchone()[0] == count
after = c.execute("SELECT id FROM ducklake_current_snapshot('lake')").fetchone()[0]
stats = c.execute('SELECT count(*),sum(file_size_bytes) FROM __ducklake_metadata_lake.main.ducklake_data_file WHERE end_snapshot IS NULL').fetchone()
report = {'snapshot_before': before, 'snapshot_after': after, 'passes': passes, 'files': stats[0], 'bytes': stats[1], 'no_more_merge_work': True}
c.close()
(root/'followup.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'variant': root.name, **report}),flush=True)
