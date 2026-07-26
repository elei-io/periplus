from __future__ import annotations

import asyncio
import unittest

import httpx

from atlas_sdk import (
    AsyncAtlasClient,
    AtlasAuthenticationError,
    AtlasClient,
    AtlasInvalidRequest,
    CompilationOutcome,
)
from atlas_sdk.compiler import AsyncCompilerClient, CompilerClient


def _result() -> dict:
    return {
        "protocol_version": 1,
        "purpose": "interactive",
        "outcome": "unchanged",
        "authored_sql": "SELECT 1",
        "executable_sql": "SELECT 1",
        "diagnostics": [],
        "compiler_version": "1.0.0",
        "valid": True,
        "supported": True,
        "materialization_eligible": False,
        "future_additive_field": "accepted",
    }


class AtlasSdkTests(unittest.TestCase):
    def test_sync_compiler_is_forward_compatible_with_additive_fields(
        self,
    ) -> None:
        http = httpx.Client(
            base_url="https://atlas.example/",
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=_result())
            ),
        )
        compiler = CompilerClient(http, analysis_timeout_seconds=10)

        result = compiler.compile("SELECT 1")

        self.assertEqual(result.outcome, CompilationOutcome.UNCHANGED)
        self.assertEqual(result.protocol_version, 1)

    def test_http_errors_remain_actionable(self) -> None:
        cases = {
            401: AtlasAuthenticationError,
            422: AtlasInvalidRequest,
        }
        for status, error in cases.items():
            with self.subTest(status=status):
                http = httpx.Client(
                    base_url="https://atlas.example/",
                    transport=httpx.MockTransport(
                        lambda _request, status=status: httpx.Response(
                            status,
                            json={"detail": "specific failure"},
                        )
                    ),
                )
                compiler = CompilerClient(http, analysis_timeout_seconds=10)
                with self.assertRaisesRegex(error, "specific|credentials"):
                    compiler.compile("SELECT 1")

    def test_top_level_clients_expose_compiler_resources(self) -> None:
        sync = AtlasClient("https://atlas.example/")
        async_client = AsyncAtlasClient("https://atlas.example/")
        self.addCleanup(sync.close)
        self.addCleanup(lambda: asyncio.run(async_client.close()))
        self.assertIsInstance(sync.compiler, CompilerClient)
        self.assertIsInstance(async_client.compiler, AsyncCompilerClient)

    def test_async_compiler(self) -> None:
        async def run() -> None:
            http = httpx.AsyncClient(
                base_url="https://atlas.example/",
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json=_result())
                ),
            )
            try:
                result = await AsyncCompilerClient(
                    http,
                    analysis_timeout_seconds=10,
                ).compile("SELECT 1")
            finally:
                await http.aclose()
            self.assertTrue(result.valid)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
