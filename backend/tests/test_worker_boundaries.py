from __future__ import annotations

import ast
import inspect
import unittest

from repository.ingestion import worker as ingestion_worker
from workers import acquisition, crawl_browser, crawl_http, ingestion, materialization


def _imported_modules(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


class WorkerBoundaryTests(unittest.TestCase):
    def test_ingestion_runtime_does_not_own_materialization(self) -> None:
        imported = _imported_modules(ingestion_worker) | _imported_modules(ingestion)

        self.assertFalse(
            any(
                name == "materialization" or name.startswith("materialization.")
                for name in imported
            )
        )
        self.assertIn("repository.catalogue.operations", imported)

    def test_materialization_process_does_not_start_ingestion(self) -> None:
        imported = _imported_modules(materialization)

        self.assertNotIn("repository.ingestion.worker", imported)

    def test_acquisition_runtime_does_not_open_control_or_catalogue_connections(self) -> None:
        imported = _imported_modules(acquisition)

        self.assertNotIn("db.session", imported)
        self.assertNotIn("repository.ingestion.worker", imported)
        self.assertNotIn("repository.service", imported)

    def test_only_browser_entrypoint_imports_browser_runtime(self) -> None:
        self.assertNotIn("crawl4ai", _imported_modules(crawl_http))
        self.assertIn("crawl4ai", _imported_modules(crawl_browser))


if __name__ == "__main__":
    unittest.main()
