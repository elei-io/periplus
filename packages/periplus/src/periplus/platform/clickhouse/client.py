"""One bounded, process-owned ClickHouse HTTP client per execution lane."""

from collections.abc import Mapping
import json
import socket
import threading
import re
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr, field_validator

from periplus.platform.config import get_str

INSERT_TARGET_BYTES = 8 * 1024 * 1024
MAX_INSERT_BYTES = 128 * 1024 * 1024


class ClickHouseConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    username: str
    password: SecretStr
    query_only: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        endpoint = urlsplit(value)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError(
                "ClickHouse URL must be an HTTP(S) origin without credentials"
            )
        # Also validate malformed/out-of-range ports before opening any connection.
        _ = endpoint.port
        return value.rstrip("/")

    @classmethod
    def from_env(cls) -> "ClickHouseConfig":
        return cls(
            url=get_str("PERIPLUS_CLICKHOUSE_URL"),
            username=get_str("PERIPLUS_CLICKHOUSE_USER"),
            password=SecretStr(get_str("PERIPLUS_CLICKHOUSE_PASSWORD")),
        )

    @classmethod
    def for_query(cls) -> "ClickHouseConfig":
        return cls(
            url=get_str("PERIPLUS_CLICKHOUSE_URL"),
            username=get_str("PERIPLUS_CLICKHOUSE_QUERY_USER"),
            password=SecretStr(get_str("PERIPLUS_CLICKHOUSE_QUERY_PASSWORD")),
            query_only=True,
        )


class ClickHouseError(RuntimeError):
    """Safe failure identity; SQL, parameters and server internals stay private."""

    def __init__(self, query_id: str, *, code: str | None = None) -> None:
        self.query_id = query_id
        self.code = code
        super().__init__(
            f"ClickHouse operation failed (query_id={query_id}, code={code or 'unknown'})"
        )


class ClickHouseClient:
    """Bounded HTTP requests without automatic retries or session state.

    An execution lane owns this client and closes it after all in-flight work ends.
    Callers own SQL, typed server parameters, batching and uncertain-write recovery.
    """

    def __init__(
        self, config: ClickHouseConfig, *, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._query_only = config.query_only
        self._socket_lock = threading.Lock()
        self._active_socket: socket.socket | None = None
        self._input_schemas: dict[str, dict[str, str]] = {}
        self._http = httpx.Client(
            base_url=config.url,
            auth=(config.username, config.password.get_secret_value()),
            timeout=httpx.Timeout(60, connect=5, pool=5),
            limits=httpx.Limits(
                max_connections=1,
                max_keepalive_connections=0 if config.query_only else 1,
            ),
            transport=transport
            or httpx.HTTPTransport(
                retries=0,
                limits=httpx.Limits(
                    max_connections=1,
                    max_keepalive_connections=0 if config.query_only else 1,
                ),
            ),
            follow_redirects=False,
            trust_env=False,
        )

    def execute(
        self,
        sql: str,
        *,
        parameters: Mapping[str, str] | None = None,
        data: bytes = b"",
        query_id: str | None = None,
        max_response_bytes: int = 8 * 1024 * 1024,
        timeout_seconds: float = 45,
        cancelled: threading.Event | None = None,
        max_request_bytes: int = 32 * 1024 * 1024,
    ) -> bytes:
        if not 0 < max_request_bytes <= MAX_INSERT_BYTES or max_response_bytes <= 0:
            raise ValueError("Invalid ClickHouse request/response byte bound")
        if len(data or sql.encode()) > max_request_bytes:
            raise ValueError(
                f"ClickHouse request is {len(data or sql.encode())} bytes; limit is {max_request_bytes} bytes"
            )
        if not 0 < timeout_seconds <= 45:
            raise ValueError("ClickHouse timeout must be within 45 seconds")
        identity = query_id or str(uuid4())
        params = {
            "query": sql,
            "query_id": identity,
            "wait_end_of_query": "1",
            "max_execution_time": str(timeout_seconds),
            "async_insert": "0",
            "date_time_input_format": "best_effort",
            "output_format_json_quote_64bit_integers": "0",
            **{f"param_{key}": value for key, value in (parameters or {}).items()},
        }
        if self._query_only:
            for setting in (
                "async_insert",
                "date_time_input_format",
                "output_format_json_quote_64bit_integers",
            ):
                params.pop(setting)
        if not data:
            data = sql.encode()
            params.pop("query")

        def trace(event, info):
            if event in {
                "connection.connect_tcp.complete",
                "connection.start_tls.complete",
            }:
                stream = info["return_value"]
                with self._socket_lock:
                    self._active_socket = stream.get_extra_info("socket")
                if cancelled is not None and cancelled.is_set():
                    self.interrupt_query()
                    raise TimeoutError("Query was cancelled.")

        if cancelled is not None and cancelled.is_set():
            raise TimeoutError("Query was cancelled.")
        try:
            with self._http.stream(
                "POST",
                "/",
                params=params,
                content=data,
                extensions={"trace": trace} if self._query_only else {},
                timeout=httpx.Timeout(
                    timeout_seconds + 1, connect=min(5, timeout_seconds), pool=5
                ),
            ) as response:
                if (
                    response.status_code != 200
                    or "X-ClickHouse-Exception-Code" in response.headers
                ):
                    raise ClickHouseError(
                        identity,
                        code=response.headers.get("X-ClickHouse-Exception-Code"),
                    )
                result = bytearray()
                for chunk in response.iter_bytes():
                    if len(result) + len(chunk) > max_response_bytes:
                        raise ClickHouseError(identity, code="response_limit")
                    result.extend(chunk)
                return bytes(result)
        except httpx.HTTPError:
            # A disconnected write may already be committed. Never retry here.
            if cancelled is not None and cancelled.is_set():
                raise TimeoutError("Query was cancelled.") from None
            raise ClickHouseError(identity, code="transport") from None
        finally:
            with self._socket_lock:
                self._active_socket = None

    def interrupt_query(self) -> None:
        """Interrupt only this read-only lane's current HTTP connection."""
        if not self._query_only:
            raise RuntimeError(
                "Write requests cannot be cancelled as read-only queries."
            )
        with self._socket_lock:
            if self._active_socket is not None:
                try:
                    self._active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # A response may have closed the connection concurrently.

    def query(
        self,
        sql: str,
        *,
        parameters: Mapping[str, str] | None = None,
        max_response_bytes: int = 8 * 1024 * 1024,
    ) -> dict[str, JsonValue]:
        result = json.loads(
            self.execute(
                sql + " FORMAT JSON",
                parameters=parameters,
                max_response_bytes=max_response_bytes,
            )
        )
        if not isinstance(result, dict) or "data" not in result:
            raise ValueError("ClickHouse returned an invalid JSON result")
        return result

    def insert_json(self, table: str, row: Mapping[str, JsonValue]) -> None:
        self.insert_rows(table, [row])

    def insert_rows(self, table: str, rows: list[Mapping[str, JsonValue]]) -> None:
        """One bounded typed block, with SHA-256 decoded inside the INSERT."""
        if not rows:
            return
        row = rows[0]
        if any(item.keys() != row.keys() for item in rows):
            raise ValueError("Insert block rows must have identical fields")
        if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", table):
            raise ValueError("invalid ClickHouse table identifier")
        columns = self._input_schemas.get(table)
        if columns is None:
            described = self.query(f"DESCRIBE TABLE {table}")["data"]
            columns = {item["name"]: item["type"] for item in described}
            self._input_schemas[table] = columns
        if not row or not row.keys() <= columns.keys():
            raise ValueError("row fields differ from the installed ClickHouse schema")
        structure = ", ".join(
            f"{key} {columns[key].replace('FixedString(32)', 'String')}" for key in row
        )
        structure = structure.replace("'", "''")
        selection = ", ".join(
            f"unhex({key})" if "FixedString(32)" in columns[key] else key for key in row
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(row)}) SELECT {selection} "
            f"FROM input('{structure}') FORMAT JSONEachRow"
        )
        block: list[bytes] = []
        size = 0
        for item in rows:
            encoded = (json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
            if len(encoded) > MAX_INSERT_BYTES:
                identity = item.get("document_id", item.get("capture_id", "unknown"))
                raise ValueError(f"ClickHouse row {table}/{identity} is {len(encoded)} bytes; limit is {MAX_INSERT_BYTES} bytes")
            if block and size + len(encoded) > INSERT_TARGET_BYTES:
                self.execute(sql, data=b"".join(block), max_request_bytes=MAX_INSERT_BYTES)
                block, size = [], 0
            block.append(encoded)
            size += len(encoded)
        if block:
            self.execute(sql, data=b"".join(block), max_request_bytes=MAX_INSERT_BYTES)

    def close(self) -> None:
        self._http.close()


def connect_clickhouse(config: ClickHouseConfig | None = None) -> ClickHouseClient:
    return ClickHouseClient(config or ClickHouseConfig.from_env())
