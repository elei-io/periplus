"""Run the isolated real-store rebuild protocol experiment on explicit opt-in."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(os.environ.get('PERIPLUS_TEST_CLICKHOUSE') == '1',
                     'requires disposable local ClickHouse/Postgres/JetStream')
class RebuildProtocolProofTests(unittest.TestCase):
    def test_real_store_rebuild_protocol(self):
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [sys.executable, str(root / 'scripts/rebuild_proof/proof.py'), '--run'],
            cwd=root, text=True, capture_output=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['coverage']['source_ids'], report['coverage']['target_ids'])
        self.assertEqual(report['activation']['mixed_versions'], 0)
        self.assertTrue(report['activation_guards']['recreated_consumer_rejected'])
        self.assertTrue(report['cancellation']['protection_held_until_drain'])


if __name__ == '__main__':
    unittest.main()
