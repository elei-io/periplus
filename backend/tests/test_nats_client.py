from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from runtime.nats_client import connect_nats


class NatsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_without_authentication_when_seed_is_absent(self) -> None:
        client = object()
        connect = AsyncMock(return_value=client)
        with (
            patch.dict(
                os.environ,
                {"NATS_URL": "nats://localhost:4222"},
                clear=True,
            ),
            patch("runtime.nats_client.nats.connect", connect),
        ):
            returned = await connect_nats()

        self.assertIs(returned, client)
        connect.assert_awaited_once_with(
            "nats://localhost:4222",
            connect_timeout=2,
            max_reconnect_attempts=-1,
        )

    async def test_connects_with_literal_nkey_seed(self) -> None:
        client = object()
        connect = AsyncMock(return_value=client)
        with (
            patch.dict(
                os.environ,
                {
                    "NATS_URL": "tls://nats.example.test:4222",
                    "NATS_SEED": "SUATESTSEED",
                },
                clear=True,
            ),
            patch("runtime.nats_client.nats.connect", connect),
        ):
            returned = await connect_nats(connect_timeout=4)

        self.assertIs(returned, client)
        connect.assert_awaited_once_with(
            "tls://nats.example.test:4222",
            connect_timeout=4,
            max_reconnect_attempts=-1,
            nkeys_seed_str="SUATESTSEED",
        )


if __name__ == "__main__":
    unittest.main()
