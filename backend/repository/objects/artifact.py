"""Canonical captured-artifact storage."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import BinaryIO

from repository.exceptions import RepositoryIntegrityError
from repository.objects.store import ObjectStore


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    sha256: str
    size_bytes: int

    @property
    def artifact_id(self) -> str:
        return f"sha256:{self.sha256}"

    @property
    def object_key(self) -> str:
        return artifact_object_key(self.sha256)


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    sha256: str
    object_key: str
    size_bytes: int
    created: bool

    @property
    def artifact_id(self) -> str:
        return f"sha256:{self.sha256}"


class RawArtifactRepository:
    """Store and verify exact non-HTML response bytes by content identity."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def put(self, content: BinaryIO, *, identity: ArtifactIdentity) -> StoredArtifact:
        key = identity.object_key
        if self.store.exists(key):
            self.verify(key, expected=identity)
            return StoredArtifact(
                sha256=identity.sha256,
                object_key=key,
                size_bytes=identity.size_bytes,
                created=False,
            )

        content.seek(0)
        created = self.store.put_if_absent(key, content)
        self.verify(key, expected=identity)
        return StoredArtifact(
            sha256=identity.sha256,
            object_key=key,
            size_bytes=identity.size_bytes,
            created=created,
        )

    def read_bytes(self, object_key: str, *, chunk_bytes: int = 1024 * 1024) -> bytes:
        """Return the exact artifact bytes after verifying their identity."""

        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be greater than zero")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        try:
            with self.store.open(object_key) as content:
                while chunk := content.read(chunk_bytes):
                    digest.update(chunk)
                    chunks.append(chunk)
        except RepositoryIntegrityError:
            raise
        except Exception as exc:
            raise RepositoryIntegrityError(
                f"artifact object could not be verified: {object_key}"
            ) from exc

        expected = _sha256_from_key(object_key)
        if expected is not None and digest.hexdigest() != expected:
            raise RepositoryIntegrityError(
                f"artifact object failed content-address verification: {object_key}"
            )
        return b"".join(chunks)

    def verify(
        self,
        object_key: str,
        *,
        expected: ArtifactIdentity | None = None,
        chunk_bytes: int = 1024 * 1024,
    ) -> ArtifactIdentity:
        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be greater than zero")
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with self.store.open(object_key) as content:
                while chunk := content.read(chunk_bytes):
                    digest.update(chunk)
                    size_bytes += len(chunk)
        except RepositoryIntegrityError:
            raise
        except Exception as exc:
            raise RepositoryIntegrityError(
                f"artifact object could not be verified: {object_key}"
            ) from exc

        identity = ArtifactIdentity(sha256=digest.hexdigest(), size_bytes=size_bytes)
        key_sha256 = _sha256_from_key(object_key)
        if key_sha256 is not None and identity.sha256 != key_sha256:
            raise RepositoryIntegrityError(
                f"artifact object failed content-address verification: {object_key}"
            )
        if expected is not None and identity != expected:
            raise RepositoryIntegrityError(
                f"artifact object metadata does not match captured content: {object_key}"
            )
        return identity


def artifact_object_key(sha256: str) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("artifact SHA-256 must be 64 lowercase hexadecimal characters")
    return f"raw/artifacts/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"


def _sha256_from_key(object_key: str) -> str | None:
    prefix = "raw/artifacts/sha256/"
    if not object_key.startswith(prefix):
        return None
    value = object_key.rsplit("/", 1)[-1]
    return value if len(value) == 64 else None
