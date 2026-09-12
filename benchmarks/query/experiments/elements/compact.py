import duckdb,json,time
from pathlib import Path
import argparse
p=argparse.ArgumentParser();p.add_argument('directory',type=Path);root=p.parse_args().directory.resolve()
c=duckdb.connect(config={'allow_unsigned_extensions':'true'});c.execute("LOAD '/opt/lakeducktor/ducklake.duckdb_extension'");c.execute("SET memory_limit='4GB'");c.execute('SET threads=2');c.execute("SET max_temp_directory_size='8GB'");c.execute(f"SET temp_directory='{root}/spill'")
c.execute(f"ATTACH 'ducklake:{root}/metadata.duckdb' AS periplus (DATA_PATH '{root}/data',METADATA_SCHEMA 'ducklake')");c.execute('USE periplus')
before=c.execute('SELECT count(*) FROM material.html_elements').fetchone();t=time.perf_counter();rows=c.execute("CALL ducklake_merge_adjacent_files('periplus','html_elements',schema=>'material')").fetchall();elapsed=time.perf_counter()-t
assert c.execute('SELECT count(*) FROM material.html_elements').fetchone()==before
report={'merge':rows,'elapsed_s':elapsed,'elements':before[0],'duckdb_version':duckdb.__version__,'extension':c.execute("select extension_version from duckdb_extensions() where extension_name='ducklake'").fetchall()};(root/'compaction.json').write_text(json.dumps(report,indent=2,default=str));print(json.dumps(report,default=str))
