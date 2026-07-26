from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time
import unittest
from uuid import UUID

import httpx

from repository.catalogue.duckbasin import (
    DuckBasinAuthenticationError,
    DuckBasinClientMinter,
    DuckBasinConfig,
    DuckBasinCredentialRejectedError,
    DuckBasinError,
    DuckBasinTarget,
    DuckBasinToken,
    DuckBasinUnavailableError,
    ServiceAccountTokenProvider,
    classify_quack_connection_error,
)


LAKE_ID = "e412ec7c-5016-420d-941c-cf8f1b563712"
LAKE_HEX = LAKE_ID.replace("-", "")


def _config(*, refresh_seconds: float = 60) -> DuckBasinConfig:
    return DuckBasinConfig(
        base_url="https://basin.example",
        lake="atlas",
        token_endpoint="https://basin.example/token",
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
    def __init__(self, *, failure_once: str | None = None) -> None:
        self.loaded: list[str] = []
        self.statements: list[str] = []
        self.closed = False
        self.failure_once = failure_once

    def load_extension(self, name: str) -> None:
        self.loaded.append(name)

    def execute(self, sql: str):
        self.statements.append(sql)
        if self.failure_once is not None and sql.startswith("ATTACH "):
            message = self.failure_once
            self.failure_once = None
            raise RuntimeError(message)
        return self

    def close(self) -> None:
        self.closed = True


class FakeTokens:
    def __init__(self) -> None:
        self.generation = 0
        self.invalidated: list[int] = []

    def get(self) -> DuckBasinToken:
        self.generation += 1
        return DuckBasinToken(
            f"token-{self.generation}",
            self.generation,
        )

    def invalidate(self, token: DuckBasinToken) -> None:
        self.invalidated.append(token.generation)


def _minter_with_connections(
    tokens: FakeTokens, connections: list[FakeConnection]
) -> DuckBasinClientMinter:
    session_ids = iter(("0123456789abcdef", "fedcba9876543210"))
    minter = DuckBasinClientMinter(
        _config(),
        tokens=tokens,  # type: ignore[arg-type]
        connect=lambda *_args, **_kwargs: connections.pop(0),
        session_id_factory=lambda: next(session_ids),
    )
    minter._target = DuckBasinTarget(  # noqa: SLF001 - boundary unit test
        lake_id=UUID(LAKE_ID),
        lake_slug="atlas",
        catalogue_alias="atlas",
        quack_uri=f"quack:{LAKE_HEX}.basin-quack.example:443",
        quack_scope=f"quack:{LAKE_HEX}.basin-quack.example",
        disable_ssl=False,
    )
    return minter


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

        self.assertEqual(provider.status(), ("empty", 0))
        first = provider.get()
        self.assertEqual(provider.status(), ("valid", 1))
        now[0] = 339
        cached = provider.get()
        now[0] = 340
        self.assertEqual(provider.status(), ("expired", 1))
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

    def test_invalidating_rejected_generation_fetches_a_new_token(self) -> None:
        issued: list[str] = []

        def handler(_: httpx.Request) -> httpx.Response:
            issued.append(f"token-{len(issued) + 1}")
            return _token_response(issued[-1])

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = ServiceAccountTokenProvider(_config(), client=client)

        first = provider.get()
        provider.invalidate_generation(first.generation)
        second = provider.get()

        self.assertEqual(first.generation, 1)
        self.assertEqual(second.generation, 2)
        self.assertEqual(issued, ["token-1", "token-2"])

    def test_oauth_rejection_is_distinct_from_provider_unavailability(self) -> None:
        for status in (401, 403):
            with self.subTest(status=status):
                client = httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _request, status=status: httpx.Response(status)
                    )
                )
                provider = ServiceAccountTokenProvider(
                    _config(),
                    client=client,
                )

                with self.assertRaises(DuckBasinAuthenticationError):
                    provider.get()

    def test_retry_after_and_cooldown_prevent_repeated_token_requests(self) -> None:
        now = [100.0]
        request_count = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return httpx.Response(429, headers={"Retry-After": "10"})
            return _token_response("recovered")

        provider = ServiceAccountTokenProvider(
            _config(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            monotonic=lambda: now[0],
            random_value=lambda: 0.5,
        )

        with self.assertRaises(DuckBasinUnavailableError) as first:
            provider.get()
        self.assertEqual(first.exception.retry_after_seconds, 10)
        now[0] = 109.0
        with self.assertRaises(DuckBasinUnavailableError):
            provider.get()
        self.assertEqual(request_count, 1)
        now[0] = 110.0
        self.assertEqual(provider.get().value, "recovered")
        self.assertEqual(request_count, 2)

    def test_parallel_callers_share_one_failed_token_request(self) -> None:
        request_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            with count_lock:
                request_count += 1
            time.sleep(0.05)
            return httpx.Response(500)

        provider = ServiceAccountTokenProvider(
            _config(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        def acquire() -> type[BaseException]:
            barrier.wait()
            try:
                provider.get()
            except BaseException as exc:
                return type(exc)
            raise AssertionError("token request unexpectedly succeeded")

        with ThreadPoolExecutor(max_workers=8) as executor:
            errors = list(executor.map(lambda _: acquire(), range(8)))

        self.assertEqual(request_count, 1)
        self.assertEqual(errors, [DuckBasinUnavailableError] * 8)

    def test_oauth_transport_timeout_is_recoverable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("provider timed out", request=request)

        provider = ServiceAccountTokenProvider(
            _config(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )

        with self.assertRaises(DuckBasinUnavailableError):
            provider.get()


class DuckBasinClientMinterTests(unittest.TestCase):
    def test_attach_authentication_failure_invalidates_and_retries(self) -> None:
        tokens = FakeTokens()
        minter = _minter_with_connections(
            tokens,
            [
                FakeConnection(failure_once="Authentication failed"),
                FakeConnection(),
            ],
        )

        minted = minter.mint()

        self.assertEqual(tokens.invalidated, [1])
        self.assertEqual(minted.token_generation, 2)

    def test_repeated_attach_authentication_failure_invalidates_each_token(
        self,
    ) -> None:
        tokens = FakeTokens()
        minter = _minter_with_connections(
            tokens,
            [
                FakeConnection(failure_once="Authentication failed"),
                FakeConnection(failure_once="Authentication failed"),
            ],
        )

        with self.assertRaises(DuckBasinCredentialRejectedError):
            minter.mint()

        self.assertEqual(tokens.invalidated, [1, 2])

    def test_quack_transport_messages_have_typed_recovery_errors(self) -> None:
        auth = classify_quack_connection_error(
            RuntimeError("Invalid Input Error: Authentication failed")
        )
        timeout = classify_quack_connection_error(
            RuntimeError("HTTP timeout while sending request")
        )
        reset = classify_quack_connection_error(
            RuntimeError("connection reset by peer")
        )
        invalid = classify_quack_connection_error(
            RuntimeError("invalid connection ID")
        )

        self.assertIsInstance(auth, DuckBasinCredentialRejectedError)
        for error in (timeout, reset, invalid):
            self.assertIsInstance(error, DuckBasinUnavailableError)

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

        self.assertEqual(first.catalogue_alias, "atlas")
        self.assertEqual(first.lake_slug, "atlas")
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
