import asyncio
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from atlas.entrypoints import worker as cli


class WorkerCliTests(unittest.TestCase):
    def test_every_deployment_role_has_a_runtime_module(self) -> None:
        self.assertEqual(
            cli.WORKER_MODULES,
            {
                "acquisition": "atlas.crawl.worker",
                "ingestion": "atlas.ingestion.worker",
                "materialization": "atlas.materialization.worker",
                "housekeeping": "atlas.operations.housekeeping",
            },
        )

    def test_run_dispatches_to_the_selected_role(self) -> None:
        runner = AsyncMock()
        with patch(
            "atlas.entrypoints.worker.import_module",
            return_value=SimpleNamespace(run=runner),
        ) as import_module:
            asyncio.run(cli.run("materialization"))

        import_module.assert_called_once_with("atlas.materialization.worker")
        runner.assert_awaited_once_with()

    def test_unknown_role_is_rejected_by_the_command(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.main(["unknown"])


if __name__ == "__main__":
    unittest.main()
