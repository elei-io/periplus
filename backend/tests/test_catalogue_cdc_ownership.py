from __future__ import annotations

import ast
from pathlib import Path
import unittest


class CatalogueCDCOwnershipTests(unittest.TestCase):
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
