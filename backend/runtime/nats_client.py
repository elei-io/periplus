"""Shared authenticated NATS connection boundary."""

from __future__ import annotations

from typing import Any

import nats

from config import get_optional, get_str


async def connect_nats(*, connect_timeout: int = 2):
    options: dict[str, Any] = {
        "connect_timeout": connect_timeout,
        "max_reconnect_attempts": -1,
    }
    seed = get_optional("NATS_SEED")
    if seed is not None:
        options["nkeys_seed_str"] = seed
    return await nats.connect(get_str("NATS_URL"), **options)
