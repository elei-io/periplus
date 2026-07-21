from __future__ import annotations

import ast
from pathlib import Path
import unittest


class CatalogueCDCOwnershipTests(unittest.TestCase):
    def test_only_catalogue_relay_owns_ducklake_change_consumers(self) -> None:
        backend = Path(__file__).parents[1]
        violations: list[str] = []
        for path in backend.rglob("*.py"):
            if "tests" in path.parts or ".venv" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module != "ducklake_cdc_client":
                    continue
                consumer_names = {
                    alias.name
                    for alias in node.names
                    if alias.name in {"DMLConsumer", "DDLConsumer"}
                }
                if consumer_names and "catalogue_relay" not in path.parts:
                    violations.append(
                        f"{path.relative_to(backend)} imports "
                        f"{', '.join(sorted(consumer_names))}"
                    )

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
