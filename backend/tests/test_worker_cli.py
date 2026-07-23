import asyncio
from contextlib import redirect_stderr
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from workers import cli


class WorkerCliTests(unittest.TestCase):
    def test_every_deployment_role_has_a_runtime_module(self) -> None:
        self.assertEqual(
            cli.WORKER_MODULES,
            {
                "acquisition": "workers.acquisition",
                "ingestion": "workers.ingestion",
                "catalogue-relay": "workers.catalogue_relay",
                "materialization": "workers.materialization",
                "housekeeping": "workers.housekeeping",
            },
        )

    def test_run_dispatches_to_the_selected_role(self) -> None:
        runner = AsyncMock()
        with patch(
            "workers.cli.import_module",
            return_value=SimpleNamespace(run=runner),
        ) as import_module:
            asyncio.run(cli.run("materialization"))

        import_module.assert_called_once_with("workers.materialization")
        runner.assert_awaited_once_with()

    def test_unknown_role_is_rejected_by_the_command(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            cli.main(["unknown"])


if __name__ == "__main__":
    unittest.main()
