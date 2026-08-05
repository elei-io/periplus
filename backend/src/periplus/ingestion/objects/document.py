"""Immutable non-HTML document storage and media-type detection."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib.metadata import version
from typing import BinaryIO
from uuid import UUID

from magika import Magika, MagikaError

from periplus.ingestion.objects.exceptions import RepositoryIntegrityError
from periplus.ingestion.objects.store import ObjectStore, ObjectWriteHeaders

MEDIA_TYPE_DETECTOR = "magika"


@dataclass(frozen=True, slots=True)
class MediaTypeDetection:
    media_type: str
    detector_name: str
    detector_version: str
    confidence: float


@cache
def _media_type_detector() -> Magika:
    return Magika()


def detect_media_type(content: bytes) -> MediaTypeDetection:
    detector = _media_type_detector()
    try:
        result = detector.identify_bytes(content)
    except MagikaError as exc:
        raise RepositoryIntegrityError("document media type detection failed") from exc
    return MediaTypeDetection(
        media_type=result.dl.mime_type,
        detector_name=MEDIA_TYPE_DETECTOR,
        detector_version=version("magika"),
        confidence=float(result.score),
    )


@dataclass(frozen=True, slots=True)
class ExactDocumentIdentity:
    sha256: str
    size_bytes: int

    @property
    def object_key(self) -> str:
        return document_object_key(self.sha256)


def identify_document(
    content: BinaryIO,
    *,
    maximum_bytes: int | None = None,
    chunk_bytes: int = 1024 * 1024,
) -> ExactDocumentIdentity:
    """Hash one seekable exact representation and restore its cursor."""

    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be greater than zero")
    digest = hashlib.sha256()
    size_bytes = 0
    content.seek(0)
    while chunk := content.read(chunk_bytes):
        size_bytes += len(chunk)
        if maximum_bytes is not None and size_bytes > maximum_bytes:
            raise ValueError("document exceeds the configured byte budget")
        digest.update(chunk)
    content.seek(0)
    return ExactDocumentIdentity(
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
    )


@dataclass(frozen=True, slots=True)
class StoredDocument:
    sha256: str
    object_key: str
    size_bytes: int
    created: bool


class ExactDocumentRepository:
    """Store exact response bytes by their immutable content identity."""

    def __init__(self, store: ObjectStore) -> None:
        self.store = store

    def put(
        self,
        content: BinaryIO,
        *,
        identity: ExactDocumentIdentity,
        source_url: str,
        visit_id: UUID,
        observed_at: datetime,
        content_type: str,
    ) -> StoredDocument:
        key = identity.object_key
        if self.store.exists(key):
            self.verify(key, expected=identity)
            return StoredDocument(
                sha256=identity.sha256,
                object_key=key,
                size_bytes=identity.size_bytes,
                created=False,
            )
        content.seek(0)
        created = self.store.put_if_absent(
            key,
            content,
            headers=ObjectWriteHeaders(
                content_type=content_type,
                metadata={
                    "url": source_url,
                    "visit-id": str(visit_id),
                    "observed-at": observed_at.isoformat(),
                },
            ),
        )
        self.verify(key, expected=identity)
        return StoredDocument(
            sha256=identity.sha256,
            object_key=key,
            size_bytes=identity.size_bytes,
            created=created,
        )

    def read_bytes(self, object_key: str, *, chunk_bytes: int = 1024 * 1024) -> bytes:
        return b"".join(self.iter_bytes(object_key, chunk_bytes=chunk_bytes))

    def iter_bytes(
        self,
        object_key: str,
        *,
        chunk_bytes: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """Yield exact bytes while verifying their content-addressed identity."""

        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be greater than zero")
        digest = hashlib.sha256()
        try:
            with self.store.open(object_key) as content:
                while chunk := content.read(chunk_bytes):
                    digest.update(chunk)
                    yield chunk
        except RepositoryIntegrityError:
            raise
        except Exception as exc:
            raise RepositoryIntegrityError(
                f"document object could not be verified: {object_key}"
            ) from exc
        expected = _sha256_from_key(object_key)
        if expected is not None and digest.hexdigest() != expected:
            raise RepositoryIntegrityError(
                f"document object failed content-address verification: {object_key}"
            )

    def verify(
        self,
        object_key: str,
        *,
        expected: ExactDocumentIdentity | None = None,
        chunk_bytes: int = 1024 * 1024,
    ) -> ExactDocumentIdentity:
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
                f"document object could not be verified: {object_key}"
            ) from exc
        identity = ExactDocumentIdentity(
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
        )
        key_sha256 = _sha256_from_key(object_key)
        if key_sha256 is not None and identity.sha256 != key_sha256:
            raise RepositoryIntegrityError(
                f"document object failed content-address verification: {object_key}"
            )
        if expected is not None and identity != expected:
            raise RepositoryIntegrityError(
                f"document object metadata does not match content: {object_key}"
            )
        return identity


def document_object_key(sha256: str) -> str:
    if len(sha256) != 64 or any(
        character not in "0123456789abcdef" for character in sha256
    ):
        raise ValueError("document SHA-256 must be lowercase hexadecimal")
    return f"raw/documents/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"


def _sha256_from_key(object_key: str) -> str | None:
    prefix = "raw/documents/sha256/"
    if not object_key.startswith(prefix):
        return None
    value = object_key.rsplit("/", 1)[-1]
    return value if len(value) == 64 else None
