from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from atlas.materialization.cdc.connections import connect_basin_cdc
from atlas.platform.messaging.client import connect_nats


class NatsClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connects_without_authentication_when_seed_is_absent(self) -> None:
        client = object()
        connect = AsyncMock(return_value=client)
        with (
            patch.dict(
                os.environ,
                {"ATLAS_NATS_URL": "nats://localhost:4222"},
                clear=True,
            ),
            patch("atlas.platform.messaging.client.nats.connect", connect),
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
                    "ATLAS_NATS_URL": "tls://nats.example.test:4222",
                    "ATLAS_NATS_SEED": "SUATESTSEED",
                },
                clear=True,
            ),
            patch("atlas.platform.messaging.client.nats.connect", connect),
        ):
            returned = await connect_nats(connect_timeout=4)

        self.assertIs(returned, client)
        connect.assert_awaited_once_with(
            "tls://nats.example.test:4222",
            connect_timeout=4,
            max_reconnect_attempts=-1,
            nkeys_seed_str="SUATESTSEED",
        )

    async def test_basin_connection_uses_only_basin_identity(self) -> None:
        client = object()
        connect = AsyncMock(return_value=client)
        with (
            patch.dict(
                os.environ,
                {
                    "DUCKBASIN_NATS_URL": "nats://basin.example.test:4222",
                    "DUCKBASIN_NATS_SEED": "SUBASINSEED",
                    "ATLAS_NATS_URL": "nats://atlas.example.test:4222",
                    "ATLAS_NATS_SEED": "SUATLASSEED",
                },
                clear=True,
            ),
            patch("atlas.platform.messaging.client.nats.connect", connect),
        ):
            returned = await connect_basin_cdc()

        self.assertIs(returned, client)
        connect.assert_awaited_once_with(
            "nats://basin.example.test:4222",
            connect_timeout=2,
            max_reconnect_attempts=-1,
            nkeys_seed_str="SUBASINSEED",
        )


if __name__ == "__main__":
    unittest.main()
