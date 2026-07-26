from __future__ import annotations

import ast
from pathlib import Path
import unittest

from cdc.events import DDL_SUBJECT, DML_SUBJECT_PREFIX, EVENT_STREAM


class CDCOwnershipTests(unittest.TestCase):
    def test_cdc_runtime_code_has_one_module_owner(self) -> None:
        backend = Path(__file__).parents[1]

        self.assertTrue((backend / "cdc").is_dir())
        for retired_path in (
            "catalogue_relay",
            "runtime/catalogue_events.py",
            "observability/catalogue_event_metrics.py",
            "workers/catalogue_relay.py",
        ):
            self.assertFalse((backend / retired_path).exists(), retired_path)

    def test_atlas_cdc_topology_uses_the_cdc_namespace(self) -> None:
        self.assertEqual(EVENT_STREAM, "ATLAS_CDC")
        self.assertEqual(DML_SUBJECT_PREFIX, "atlas.cdc.dml")
        self.assertEqual(DDL_SUBJECT, "atlas.cdc.ddl")

    def test_atlas_does_not_own_ducklake_cdc_consumers(self) -> None:
        backend = Path(__file__).parents[1]
        violations: list[str] = []
        for path in backend.rglob("*.py"):
            if "tests" in path.parts or ".venv" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module == "ducklake_cdc_client":
                    violations.append(
                        f"{path.relative_to(backend)} imports ducklake_cdc_client"
                    )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
