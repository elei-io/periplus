from __future__ import annotations

import ast
from pathlib import Path
import unittest


_BACKEND = Path(__file__).resolve().parents[1]
_PRODUCTION_ROOTS = (
    _BACKEND / "agents",
    _BACKEND / "api",
    _BACKEND / "catalogue_relay",
    _BACKEND / "control",
    _BACKEND / "materialization",
    _BACKEND / "repository",
    _BACKEND / "runtime",
    _BACKEND / "workers",
)
_COMPILER_FACADE = _BACKEND / "atlas_sql"
_LOW_LEVEL_CATALOGUE_MODULES = frozenset(
    {
        _BACKEND / "repository/catalogue/benchmark.py",
        _BACKEND / "repository/catalogue/client.py",
        _BACKEND / "repository/catalogue/duckbasin.py",
        _BACKEND / "repository/catalogue/query.py",
        _BACKEND / "repository/catalogue/quack_runtime.py",
        _BACKEND / "repository/catalogue/service.py",
        # Navigation owns its bounded standalone page-only connection and its
        # pinned historical catalogue connection by architectural contract.
        _BACKEND / "runtime/graph_navigation.py",
    }
)


def _production_python() -> list[Path]:
    return sorted(
        path
        for root in _PRODUCTION_ROOTS
        if root.exists()
        for path in root.rglob("*.py")
    )


class CatalogueSqlBoundaryTests(unittest.TestCase):
    def test_production_uses_public_compiler_facade(self) -> None:
        violations: list[str] = []
        for path in _production_python():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                module: str | None = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "catalogue.compiler" or alias.name.startswith(
                            "catalogue.compiler."
                        ):
                            violations.append(
                                f"{path.relative_to(_BACKEND)}:{node.lineno}"
                            )
                if module == "catalogue.compiler" or (
                    module is not None and module.startswith("catalogue.compiler.")
                ):
                    violations.append(
                        f"{path.relative_to(_BACKEND)}:{node.lineno}"
                    )
        self.assertEqual(
            violations,
            [],
            "production modules must import compiler capabilities from atlas_sql: "
            + ", ".join(violations),
        )

    def test_removed_unclassified_catalogue_apis_do_not_return(self) -> None:
        forbidden = {"remote_rows", "remote_execute"}
        violations: list[str] = []
        for path in _production_python():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in forbidden:
                        violations.append(
                            f"{path.relative_to(_BACKEND)}:{node.lineno}:{node.name}"
                        )
                if isinstance(node, ast.Attribute) and node.attr in forbidden:
                    violations.append(
                        f"{path.relative_to(_BACKEND)}:{node.lineno}:{node.attr}"
                    )
                if isinstance(node, ast.Name) and node.id in forbidden:
                    violations.append(
                        f"{path.relative_to(_BACKEND)}:{node.lineno}:{node.id}"
                    )
        self.assertEqual(
            violations,
            [],
            "unclassified catalogue execution must use an explicitly trusted "
            "API or the compiler-routed interactive boundary: "
            + ", ".join(violations),
        )

    def test_raw_catalogue_connection_execution_is_contained(self) -> None:
        violations: list[str] = []
        for path in _production_python():
            if path in _LOW_LEVEL_CATALOGUE_MODULES:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute) or node.attr != "execute":
                    continue
                owner = node.value
                if isinstance(owner, ast.Attribute) and owner.attr in {
                    "connection",
                    "trusted_connection",
                }:
                    violations.append(
                        f"{path.relative_to(_BACKEND)}:{node.lineno}"
                    )
        self.assertEqual(
            violations,
            [],
            "raw catalogue execution belongs behind repository catalogue "
            "boundaries: "
            + ", ".join(violations),
        )

    def test_public_compiler_facade_is_the_only_internal_importer(self) -> None:
        importers: list[str] = []
        for path in _BACKEND.rglob("*.py"):
            if ".venv" in path.parts or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            if any(
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (
                    node.module == "catalogue.compiler"
                    or node.module.startswith("catalogue.compiler.")
                )
                for node in ast.walk(tree)
            ):
                if _COMPILER_FACADE not in path.parents:
                    importers.append(str(path.relative_to(_BACKEND)))
        self.assertEqual(importers, [])


if __name__ == "__main__":
    unittest.main()
