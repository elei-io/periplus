"""Object storage with identical relative-key semantics on disk and S3."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol, runtime_checkable

from botocore.exceptions import ClientError

from repository.exceptions import RepositoryKeyError, RepositoryObjectNotFound


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal repository object-store contract."""

    def put_if_absent(self, key: str, content: BinaryIO) -> bool:
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


class FileObjectStore:
    """Atomic content-addressed object storage rooted at a local directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_if_absent(self, key: str, content: BinaryIO) -> bool:
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

    def put_if_absent(self, key: str, content: BinaryIO) -> bool:
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=self._object_key(key),
                Body=content,
                IfNoneMatch="*",
            )
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
