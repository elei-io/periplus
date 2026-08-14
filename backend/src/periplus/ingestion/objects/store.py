"""Object storage with identical relative-key semantics on disk and S3."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping, Protocol, runtime_checkable

from botocore.exceptions import ClientError

from periplus.ingestion.objects.exceptions import RepositoryKeyError, RepositoryObjectNotFound


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    size: int
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class ObjectWriteHeaders:
    """HTTP representation headers attached when an object is first stored."""

    content_type: str
    content_encoding: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal repository object-store contract."""

    def put_if_absent(
        self,
        key: str,
        content: BinaryIO,
        *,
        headers: ObjectWriteHeaders | None = None,
    ) -> bool:
        """Store content only when key is absent; return whether it was created."""

    def open(self, key: str) -> AbstractContextManager[BinaryIO]:
        """Open an object for streaming binary reads."""

    def exists(self, key: str) -> bool:
        """Return whether key exists."""

    def size(self, key: str) -> int:
        """Return the object size in bytes."""

    def delete(self, key: str) -> bool:
        """Delete key and return whether it previously existed."""

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object below a normalized runtime prefix."""

    def list_objects(self, prefix: str) -> Iterator[ObjectMetadata]:
        """List object metadata below a normalized runtime prefix."""

    def delete_many(self, keys: tuple[str, ...]) -> int:
        """Delete a bounded group of normalized keys."""


class FileObjectStore:
    """Atomic content-addressed object storage rooted at a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_if_absent(
        self,
        key: str,
        content: BinaryIO,
        *,
        headers: ObjectWriteHeaders | None = None,
    ) -> bool:
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as output:
                while chunk := content.read(1024 * 1024):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return False
            return True
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        try:
            with self._path(key).open("rb") as content:
                yield content
        except FileNotFoundError as exc:
            raise RepositoryObjectNotFound(key) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def size(self, key: str) -> int:
        try:
            return self._path(key).stat().st_size
        except FileNotFoundError as exc:
            raise RepositoryObjectNotFound(key) from exc

    def delete(self, key: str) -> bool:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            return False
        return True

    def delete_prefix(self, prefix: str) -> int:
        root = self._path(prefix.rstrip("/"))
        if not root.exists():
            return 0
        deleted = 0
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink()
                deleted += 1
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        root.rmdir()
        return deleted

    def list_objects(self, prefix: str) -> Iterator[ObjectMetadata]:
        root = self._path(prefix.rstrip("/"))
        if not root.exists():
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            yield ObjectMetadata(
                key=path.relative_to(self.root).as_posix(),
                size=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )

    def delete_many(self, keys: tuple[str, ...]) -> int:
        return sum(self.delete(key) for key in keys)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*_key_parts(key))


class S3ObjectStore:
    """Conditional-write object storage under an S3-compatible bucket prefix."""

    def __init__(self, client: object, *, bucket: str, prefix: str = "") -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket must not be empty")
        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")

    def put_if_absent(
        self,
        key: str,
        content: BinaryIO,
        *,
        headers: ObjectWriteHeaders | None = None,
    ) -> bool:
        options: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": self._object_key(key),
            "Body": content,
            "IfNoneMatch": "*",
        }
        if headers is not None:
            options["ContentType"] = headers.content_type
            if headers.content_encoding is not None:
                options["ContentEncoding"] = headers.content_encoding
            if headers.metadata:
                options["Metadata"] = dict(headers.metadata)
        try:
            self.client.put_object(**options)
        except ClientError as exc:
            if _status_code(exc) in {409, 412}:
                return False
            raise
        return True

    @contextmanager
    def open(self, key: str) -> Iterator[BinaryIO]:
        try:
            response = self.client.get_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
        except ClientError as exc:
            if _status_code(exc) == 404 or _error_code(exc) in {"NoSuchKey", "NotFound"}:
                raise RepositoryObjectNotFound(key) from exc
            raise
        body = response["Body"]
        try:
            yield body
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._object_key(key))
        except ClientError as exc:
            if _status_code(exc) == 404 or _error_code(exc) in {"NoSuchKey", "NotFound"}:
                return False
            raise
        return True

    def size(self, key: str) -> int:
        try:
            response = self.client.head_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
            )
        except ClientError as exc:
            if _status_code(exc) == 404 or _error_code(exc) in {"NoSuchKey", "NotFound"}:
                raise RepositoryObjectNotFound(key) from exc
            raise
        return int(response["ContentLength"])

    def delete(self, key: str) -> bool:
        existed = self.exists(key)
        if existed:
            self.client.delete_object(Bucket=self.bucket, Key=self._object_key(key))
        return existed

    def delete_prefix(self, prefix: str) -> int:
        object_prefix = self._object_key(prefix.rstrip("/")) + "/"
        deleted = 0
        continuation = None
        while True:
            options = {"Bucket": self.bucket, "Prefix": object_prefix}
            if continuation is not None:
                options["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**options)
            objects = [{"Key": item["Key"]} for item in response.get("Contents", [])]
            if objects:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
                deleted += len(objects)
            if not response.get("IsTruncated"):
                break
            continuation = response["NextContinuationToken"]
        return deleted

    def list_objects(self, prefix: str) -> Iterator[ObjectMetadata]:
        relative_prefix = prefix.rstrip("/")
        object_prefix = self._object_key(relative_prefix) + "/"
        continuation = None
        while True:
            options = {"Bucket": self.bucket, "Prefix": object_prefix}
            if continuation is not None:
                options["ContinuationToken"] = continuation
            response = self.client.list_objects_v2(**options)
            for item in response.get("Contents", []):
                object_key = str(item["Key"])
                relative_key = (
                    object_key[len(self.prefix) + 1 :]
                    if self.prefix
                    else object_key
                )
                modified = item["LastModified"]
                if modified.tzinfo is None:
                    modified = modified.replace(tzinfo=UTC)
                yield ObjectMetadata(
                    key=relative_key,
                    size=int(item["Size"]),
                    last_modified=modified.astimezone(UTC),
                )
            if not response.get("IsTruncated"):
                break
            continuation = response["NextContinuationToken"]

    def delete_many(self, keys: tuple[str, ...]) -> int:
        deleted = 0
        for offset in range(0, len(keys), 1000):
            batch = keys[offset : offset + 1000]
            if not batch:
                continue
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [
                        {"Key": self._object_key(key)} for key in batch
                    ],
                    "Quiet": True,
                },
            )
            errors = response.get("Errors", [])
            if errors:
                raise RuntimeError(
                    f"S3 batch deletion failed for {len(errors)} objects"
                )
            deleted += len(batch)
        return deleted

    def _object_key(self, key: str) -> str:
        relative = "/".join(_key_parts(key))
        return f"{self.prefix}/{relative}" if self.prefix else relative


def _key_parts(key: str) -> tuple[str, ...]:
    if not key or "\\" in key:
        raise RepositoryKeyError(f"invalid repository object key: {key!r}")
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RepositoryKeyError(f"invalid repository object key: {key!r}")
    normalized = path.as_posix()
    if normalized != key:
        raise RepositoryKeyError(f"repository object key is not normalized: {key!r}")
    return path.parts


def _status_code(exc: ClientError) -> int | None:
    value = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return int(value) if value is not None else None


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))
