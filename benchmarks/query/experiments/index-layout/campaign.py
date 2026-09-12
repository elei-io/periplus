"""Serial, resumable isolated matrix. Never runs against production storage."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--phase', choices=('matrix', 'rowgroups', 'growth', 'fragmentation', 'files', 'appendgrowth'), required=True)
args = p.parse_args()
root = args.root.resolve()
script = Path(__file__).with_name('compare.py')
shapes = ('term', 'content', 'node')
if args.phase == 'matrix':
    specs = [(f'icu-{shape}-b{buckets}', shape, buckets, 10, 1, 122880) for buckets in (0, 8, 32) for shape in shapes]
elif args.phase == 'rowgroups':
    specs = [(f'icu-{shape}-b8-r2048', shape, 8, 10, 1, 2048) for shape in shapes]
elif args.phase == 'growth':
    specs = [(f'icu-{shape}-b8-x{copies}', shape, 8, 10, copies, 2048) for copies in (2, 4) for shape in shapes]
elif args.phase == 'fragmentation':
    specs = [(f'icu-{shape}-b8-n{batches}', shape, 8, batches, 1, 2048) for batches in (100, 400) for shape in shapes]
elif args.phase == 'appendgrowth':
    specs = [(f'icu-{shape}-b8-x4-n40', shape, 8, 40, 4, 2048) for shape in shapes]
else:
    specs = [(f'icu-{shape}-b8-x4-smallfiles', shape, 8, 10, 4, 2048) for shape in shapes]


def run(out, action, *extra):
    subprocess.run([sys.executable, str(script), action, '--output', str(out), *extra], check=True)


for name, shape, buckets, batches, copies, rowgroups in specs:
    out = root / name
    if not (out / 'build.json').exists():
        run(out, 'build', '--source', str(root / 'icu-source'), '--shape', shape, '--buckets', str(buckets), '--batches', str(batches), '--copies', str(copies), '--row-group-size', str(rowgroups), '--target-file-size', '1MB' if args.phase == 'files' else '64MB')
    if not (out / 'queries-fresh-0.json').exists():
        run(out, 'query')
    if not (out / 'compaction.json').exists():
        try:
            run(out, 'compact')
        except subprocess.CalledProcessError:
            (out / 'maintenance-failure.json').write_text(json.dumps({'complete': False, 'reason': 'See campaign log; failed native maintenance is not an accepted result'}) + '\n')
            continue
    if not (out / 'queries-maintained-0.json').exists():
        run(out, 'query', '--state', 'maintained')

# Reverse variant order and query order, using independent connections.
for name, *_ in reversed(specs):
    out = root / name
    if (out / 'compaction.json').exists() and not (out / 'queries-maintained-1.json').exists():
        run(out, 'query', '--state', 'maintained', '--reverse')
