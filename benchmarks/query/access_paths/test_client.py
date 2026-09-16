import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import client


class TransportTests(unittest.TestCase):
    def test_timeout_preserves_query_identity_and_never_replays_insert(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(client, "ARTIFACTS", Path(root)),
            patch.object(
                client.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("test", 1),
            ) as run,
        ):
            result = client.execute("INSERT INTO isolated VALUES (1)", read=False)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(result["exit"], 124)
            saved = json.loads((Path(root) / "operations.jsonl").read_text())
            self.assertEqual(saved["query_id"], result["query_id"])
