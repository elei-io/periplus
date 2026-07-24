from __future__ import annotations

import ast
from pathlib import Path
import unittest


_SDK = Path(__file__).resolve().parents[2] / "sdk"


class AtlasSdkBoundaryTests(unittest.TestCase):
    def test_sdk_has_no_backend_or_embedded_engine_imports(self) -> None:
        forbidden = {
            "catalogue",
            "duckdb",
            "sqlglot",
            "repository",
            "runtime",
        }
        violations: list[str] = []
        for path in (_SDK / "src").rglob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                for name in names:
                    if name.split(".", 1)[0] in forbidden:
                        violations.append(
                            f"{path.relative_to(_SDK)}:{node.lineno}:{name}"
                        )
        self.assertEqual(violations, [])

    def test_sdk_declares_typing_and_independent_project_metadata(self) -> None:
        self.assertTrue((_SDK / "src/atlas_sdk/py.typed").exists())
        pyproject = (_SDK / "pyproject.toml").read_text()
        self.assertIn('name = "atlas-sdk"', pyproject)
        self.assertIn('requires-python = ">=3.10"', pyproject)


if __name__ == "__main__":
    unittest.main()
