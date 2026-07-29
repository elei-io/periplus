"""Durable raw-HTTP client for DuckBasin managed bulk commits."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict

from atlas.platform.catalogue.duckbasin import (
    DuckBasinAuthenticationError,
    DuckBasinConfig,
    DuckBasinError,
    DuckBasinProtocolError,
    DuckBasinTarget,
    DuckBasinUnavailableError,
    ServiceAccountTokenProvider,
    _retry_after_seconds,
)


BulkCommitState = Literal[
    "pending",
    "preparing",
    "prepared",
    "submitted",
    "validating",
    "committing",
    "committed",
    "failed",
    "aborted",
    "expired",
]
_WAITING_FOR_UPLOAD = frozenset({"pending", "preparing"})
_COMMITTING = frozenset({"submitted", "validating", "committing"})
_TERMINAL_FAILURES = frozenset({"failed", "aborted", "expired"})


class DuckBasinBulkCommitError(DuckBasinError):
    """Basin permanently rejected or terminated one bulk operation."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class BulkPartitionValue:
    key: str
    value: str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class BulkCommitFile:
    file_id: str
    schema: str
    table: str
    ingest_mode: Literal["register", "copy"]
    mutation_mode: Literal["append", "replace", "delete"]
    path: Path
    rows: int
    sha256: str
    md5: str
    match_columns: tuple[str, ...] = ()
    partition_values: tuple[BulkPartitionValue, ...] = ()

    @property
    def bytes(self) -> int:
        return self.path.stat().st_size

    def manifest(self) -> dict[str, object]:
        return {
            "file_id": self.file_id,
            "schema": self.schema,
            "table": self.table,
            "ingest_mode": self.ingest_mode,
            "mutation_mode": self.mutation_mode,
            "match_columns": list(self.match_columns),
            "bytes": self.bytes,
            "rows": self.rows,
            "sha256": self.sha256,
            "md5": self.md5,
            "partition_values": [
                {"key": item.key, "value": item.value}
                for item in self.partition_values
            ],
        }


@dataclass(frozen=True, slots=True)
class BulkCatalogueAction:
    action_id: str
    kind: Literal["clone_tables", "swap_tables", "drop_tables"]
    entries: tuple[Mapping[str, str], ...]

    def manifest(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "kind": self.kind,
            "entries": [
                dict(sorted(entry.items()))
                for entry in self.entries
            ],
        }


class _BulkUpload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy: Literal["single_put"]
    method: Literal["PUT"]
    url: str
    headers: dict[str, str]


class _BulkOperationFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    file_id: str
    upload: _BulkUpload | None = None


class BulkCommitOperation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    id: UUID
    ducklake: UUID
    idempotency_key: str
    manifest_hash: str
    state: BulkCommitState
    snapshot_id: int | None = None
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    files: tuple[_BulkOperationFile, ...]


class DuckBasinBulkClient:
    """Commit bounded local Parquet files through Basin-owned storage."""

    def __init__(
        self,
        config: DuckBasinConfig,
        target: DuckBasinTarget,
        tokens: ServiceAccountTokenProvider,
        *,
        client: httpx.Client | None = None,
        poll_interval_seconds: float = 0.25,
        operation_timeout_seconds: float = 300,
        upload_parallelism: int = 8,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if poll_interval_seconds <= 0 or operation_timeout_seconds <= 0:
            raise ValueError("bulk operation timing bounds must be positive")
        if upload_parallelism < 1:
            raise ValueError("bulk upload parallelism must be positive")
        self._config = config
        self._target = target
        self._tokens = tokens
        self._client = client or httpx.Client(
            timeout=config.request_timeout_seconds
        )
        self._owns_client = client is None
        self._poll_interval_seconds = poll_interval_seconds
        self._operation_timeout_seconds = operation_timeout_seconds
        self._upload_parallelism = upload_parallelism
        self._monotonic = monotonic
        self._sleep = sleep

    def commit(
        self,
        files: Sequence[BulkCommitFile] = (),
        *,
        idempotency_key: str,
        actions: Sequence[BulkCatalogueAction] = (),
    ) -> BulkCommitOperation:
        """Upload and atomically register one immutable manifest."""

        selected = tuple(files)
        selected_actions = tuple(actions)
        if not selected and not selected_actions:
            raise ValueError(
                "a bulk commit requires at least one file or action"
            )
        if not idempotency_key:
            raise ValueError("a bulk commit requires an idempotency key")
        by_id = {item.file_id: item for item in selected}
        if len(by_id) != len(selected):
            raise ValueError("bulk commit file IDs must be unique")
        for item in selected:
            if not item.path.is_file():
                raise ValueError(f"bulk commit file does not exist: {item.path}")

        deadline = self._monotonic() + self._operation_timeout_seconds
        operation = self._operation_response(
            self._request(
                "POST",
                self._collection_path,
                json={
                    "idempotency_key": idempotency_key,
                    "files": [item.manifest() for item in selected],
                    "actions": [
                        item.manifest()
                        for item in selected_actions
                    ],
                },
            )
        )
        operation = self._await_upload_or_commit(operation, deadline)
        if operation.state == "committed":
            return self._require_snapshot(operation)
        if operation.state in _COMMITTING:
            return self._await_committed(operation, deadline)
        if operation.state != "prepared":
            raise DuckBasinProtocolError(
                f"DuckBasin bulk operation entered unexpected state "
                f"{operation.state!r}"
            )

        uploads = {item.file_id: item.upload for item in operation.files}
        if set(uploads) != set(by_id):
            raise DuckBasinProtocolError(
                "DuckBasin prepared a different bulk file set"
            )
        if any(upload is None for upload in uploads.values()):
            raise DuckBasinProtocolError(
                "DuckBasin prepared a bulk file without an upload target"
            )
        if selected:
            with ThreadPoolExecutor(
                max_workers=min(self._upload_parallelism, len(selected)),
                thread_name_prefix="basin-upload",
            ) as executor:
                futures = [
                    executor.submit(
                        self._upload,
                        by_id[file_id],
                        upload,
                    )
                    for file_id, upload in sorted(uploads.items())
                ]
                for future in futures:
                    future.result()

        operation = self._operation_response(
            self._request(
                "POST",
                f"{self._operation_path(operation.id)}complete/",
            )
        )
        return self._await_committed(operation, deadline)

    @property
    def _collection_path(self) -> str:
        return f"/api/ducklakes/{self._target.lake_id}/bulk-commits/"

    def _operation_path(self, operation_id: UUID) -> str:
        return f"{self._collection_path}{operation_id}/"

    def _await_upload_or_commit(
        self,
        operation: BulkCommitOperation,
        deadline: float,
    ) -> BulkCommitOperation:
        while operation.state in _WAITING_FOR_UPLOAD:
            self._wait(deadline)
            operation = self._get_operation(operation.id)
        self._raise_terminal(operation)
        return operation

    def _await_committed(
        self,
        operation: BulkCommitOperation,
        deadline: float,
    ) -> BulkCommitOperation:
        while operation.state != "committed":
            self._raise_terminal(operation)
            if operation.state not in _COMMITTING:
                raise DuckBasinProtocolError(
                    f"DuckBasin bulk operation entered unexpected state "
                    f"{operation.state!r} after submission"
                )
            self._wait(deadline)
            operation = self._get_operation(operation.id)
        return self._require_snapshot(operation)

    def _wait(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            raise DuckBasinUnavailableError(
                "DuckBasin bulk operation did not finish before its deadline"
            )
        self._sleep(
            min(
                self._poll_interval_seconds,
                max(0, deadline - self._monotonic()),
            )
        )

    def _get_operation(self, operation_id: UUID) -> BulkCommitOperation:
        return self._operation_response(
            self._request("GET", self._operation_path(operation_id))
        )

    def _upload(
        self,
        item: BulkCommitFile,
        upload: _BulkUpload | None,
    ) -> None:
        if upload is None:
            raise DuckBasinProtocolError(
                f"DuckBasin omitted the upload for {item.file_id}"
            )
        try:
            with item.path.open("rb") as body:
                response = self._client.request(
                    upload.method,
                    upload.url,
                    headers=upload.headers,
                    content=body,
                )
        except (OSError, httpx.HTTPError) as exc:
            raise DuckBasinUnavailableError(
                f"Bulk upload failed for {item.file_id}"
            ) from exc
        if response.is_error:
            raise DuckBasinUnavailableError(
                f"Bulk upload failed for {item.file_id} "
                f"(HTTP {response.status_code})"
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        token = self._tokens.get()
        response = self._send(method, path, token.value, json=json)
        if response.status_code == 401:
            self._tokens.invalidate(token)
            token = self._tokens.get()
            response = self._send(method, path, token.value, json=json)
        if response.status_code in {401, 403}:
            raise DuckBasinAuthenticationError(
                "DuckBasin rejected the bulk-commit credential "
                f"(HTTP {response.status_code})"
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise DuckBasinUnavailableError(
                f"DuckBasin bulk API is unavailable for {path} "
                f"(HTTP {response.status_code})",
                retry_after_seconds=_retry_after_seconds(response),
            )
        if response.is_error:
            error_code, message, retryable = self._error_payload(response)
            raise DuckBasinBulkCommitError(
                message,
                error_code=error_code,
                retryable=retryable,
            )
        return response

    def _send(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: Mapping[str, object] | None,
    ) -> httpx.Response:
        try:
            return self._client.request(
                method,
                f"{self._config.base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )
        except httpx.HTTPError as exc:
            raise DuckBasinUnavailableError(
                f"DuckBasin bulk API is unavailable for {path}"
            ) from exc

    @staticmethod
    def _operation_response(response: httpx.Response) -> BulkCommitOperation:
        try:
            return BulkCommitOperation.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise DuckBasinProtocolError(
                "DuckBasin returned an invalid bulk operation"
            ) from exc

    @staticmethod
    def _error_payload(
        response: httpx.Response,
    ) -> tuple[str, str, bool]:
        try:
            payload = response.json()
        except ValueError:
            return (
                "HTTP_ERROR",
                f"DuckBasin rejected the bulk operation "
                f"(HTTP {response.status_code})",
                False,
            )
        if not isinstance(payload, dict):
            return ("HTTP_ERROR", "DuckBasin rejected the bulk operation", False)
        return (
            str(payload.get("error_code") or "HTTP_ERROR"),
            str(
                payload.get("detail")
                or payload.get("error_message")
                or "DuckBasin rejected the bulk operation"
            ),
            bool(payload.get("retryable", False)),
        )

    @staticmethod
    def _raise_terminal(operation: BulkCommitOperation) -> None:
        if operation.state not in _TERMINAL_FAILURES:
            return
        raise DuckBasinBulkCommitError(
            operation.error_message
            or f"DuckBasin bulk operation ended as {operation.state}",
            error_code=operation.error_code or operation.state.upper(),
            retryable=operation.retryable,
        )

    @staticmethod
    def _require_snapshot(
        operation: BulkCommitOperation,
    ) -> BulkCommitOperation:
        if operation.snapshot_id is None:
            raise DuckBasinProtocolError(
                "DuckBasin committed a bulk operation without a snapshot"
            )
        return operation

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> DuckBasinBulkClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
