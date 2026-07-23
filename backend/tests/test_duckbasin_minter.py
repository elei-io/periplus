from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest

import httpx

from repository.catalogue.duckbasin import (
    DuckBasinClientMinter,
    DuckBasinConfig,
    DuckBasinError,
    ServiceAccountTokenProvider,
)


LAKE_ID = "e412ec7c-5016-420d-941c-cf8f1b563712"
LAKE_HEX = LAKE_ID.replace("-", "")


def _config(*, refresh_seconds: float = 60) -> DuckBasinConfig:
    return DuckBasinConfig(
        base_url="https://basin.example",
        lake="atlas",
        token_endpoint="https://basin.example/token",
        service_account="atlas-production",
        client_id="client-id",
        client_secret="client-secret",
        request_timeout_seconds=1,
        token_refresh_seconds=refresh_seconds,
    )


def _token_response(token: str, *, expires_in: int = 300) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
        },
    )


class FakeConnection:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.statements: list[str] = []
        self.closed = False

    def load_extension(self, name: str) -> None:
        self.loaded.append(name)

    def execute(self, sql: str):
        self.statements.append(sql)
        return self

    def close(self) -> None:
        self.closed = True


class ServiceAccountTokenProviderTests(unittest.TestCase):
    def test_caches_then_refreshes_before_expiry(self) -> None:
        now = [100.0]
        issued: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertIn(
                b"grant_type=client_credentials",
                request.content,
            )
            issued.append(f"token-{len(issued) + 1}")
            return _token_response(issued[-1], expires_in=300)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = ServiceAccountTokenProvider(
            _config(refresh_seconds=60),
            client=client,
            monotonic=lambda: now[0],
        )

        first = provider.get()
        now[0] = 339
        cached = provider.get()
        now[0] = 340
        refreshed = provider.get()

        self.assertIs(cached, first)
        self.assertEqual(first.generation, 1)
        self.assertEqual(refreshed.generation, 2)
        self.assertEqual(issued, ["token-1", "token-2"])

    def test_parallel_callers_share_one_token_request(self) -> None:
        request_count = 0
        count_lock = threading.Lock()

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal request_count
            with count_lock:
                request_count += 1
            time.sleep(0.05)
            return _token_response("shared-token")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = ServiceAccountTokenProvider(_config(), client=client)
        barrier = threading.Barrier(8)

        def acquire() -> object:
            barrier.wait()
            return provider.get()

        with ThreadPoolExecutor(max_workers=8) as executor:
            leases = list(executor.map(lambda _: acquire(), range(8)))

        self.assertEqual(request_count, 1)
        self.assertTrue(all(lease is leases[0] for lease in leases))


class DuckBasinClientMinterTests(unittest.TestCase):
    def test_mints_unique_session_affine_connections(self) -> None:
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/token":
                return _token_response("access-token")
            if request.url.path == "/api/ducklakes/":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": LAKE_ID,
                            "slug": "atlas",
                            "connectable": True,
                        }
                    ],
                )
            if request.url.path == f"/api/ducklakes/{LAKE_ID}/connection/":
                return httpx.Response(
                    200,
                    json={
                        "id": LAKE_ID,
                        "catalog_alias": "atlas",
                        "quack_uri": (
                            f"quack:{LAKE_HEX}.basin-quack.example:443"
                        ),
                        "secret_scope": (
                            f"quack:{LAKE_HEX}.basin-quack.example"
                        ),
                        "disable_ssl": False,
                    },
                )
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        tokens = ServiceAccountTokenProvider(_config(), client=client)
        connections: list[FakeConnection] = []

        def connect(*_: object, **__: object) -> FakeConnection:
            connection = FakeConnection()
            connections.append(connection)
            return connection

        session_ids = iter(
            ("0123456789abcdef", "fedcba9876543210")
        )
        minter = DuckBasinClientMinter(
            _config(),
            client=client,
            tokens=tokens,
            connect=connect,
            session_id_factory=lambda: next(session_ids),
        )

        first = minter.mint()
        second = minter.mint()

        self.assertEqual(first.lake_id.hex, LAKE_HEX)
        self.assertEqual(first.catalogue_alias, "atlas")
        self.assertEqual(first.token_generation, 1)
        self.assertIn(
            f"quack:0123456789abcdef-{LAKE_HEX}.basin-quack.example:443",
            first.quack_uri,
        )
        self.assertNotEqual(first.session_id, second.session_id)
        self.assertEqual(connections[0].loaded, ["quack"])
        self.assertTrue(
            any(
                f"SCOPE 'quack:0123456789abcdef-{LAKE_HEX}."
                "basin-quack.example'" in statement
                for statement in connections[0].statements
            )
        )
        self.assertTrue(
            any(
                'AS "atlas" (TYPE quack)' in statement
                for statement in connections[0].statements
            )
        )
        self.assertEqual(connections[0].statements[-1], 'USE "atlas"')
        self.assertNotIn("access-token", repr(first))
        self.assertEqual(
            requests.count(("GET", "/api/ducklakes/")),
            1,
        )
        self.assertEqual(
            requests.count(
                ("GET", f"/api/ducklakes/{LAKE_ID}/connection/")
            ),
            1,
        )
        self.assertEqual(requests.count(("POST", "/token")), 1)

        first.close()
        first.close()
        second.close()
        self.assertTrue(all(connection.closed for connection in connections))

    def test_rejects_noncanonical_target_routing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return _token_response("access-token")
            if request.url.path == "/api/ducklakes/":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": LAKE_ID,
                            "slug": "atlas",
                            "connectable": True,
                        }
                    ],
                )
            return httpx.Response(
                200,
                json={
                    "id": LAKE_ID,
                    "catalog_alias": "atlas",
                    "quack_uri": "quack:another-lake.example:443",
                    "secret_scope": "quack:another-lake.example",
                    "disable_ssl": False,
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        tokens = ServiceAccountTokenProvider(_config(), client=client)
        minter = DuckBasinClientMinter(
            _config(),
            client=client,
            tokens=tokens,
        )

        with self.assertRaisesRegex(DuckBasinError, "canonical lake identity"):
            minter.target()


if __name__ == "__main__":
    unittest.main()
