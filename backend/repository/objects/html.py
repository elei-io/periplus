"""Canonical captured-HTML storage."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import zstandard

from repository.objects.store import ObjectStore, ObjectWriteHeaders
from repository.exceptions import RepositoryIntegrityError


@dataclass(frozen=True)
class StoredHtml:
    sha256: str
    object_key: str
    size_bytes: int
    compressed_size_bytes: int
    created: bool
    compression: str = "zstd"
    content_type: str = "text/html"
    encoding: str = "utf-8"


@dataclass(frozen=True, slots=True)
class HtmlIdentity:
    sha256: str
    size_bytes: int

    @property
    def object_key(self) -> str:
        return html_object_key(self.sha256)


def identify_html(captured_html: str, *, chunk_chars: int = 1_048_576) -> HtmlIdentity:
    """Return canonical identity without requiring an object-store instance."""

    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be greater than zero")
    digest = hashlib.sha256()
    size_bytes = 0
    for offset in range(0, len(captured_html), chunk_chars):
        encoded = captured_html[offset : offset + chunk_chars].encode("utf-8")
        digest.update(encoded)
        size_bytes += len(encoded)
    return HtmlIdentity(sha256=digest.hexdigest(), size_bytes=size_bytes)


class RawHtmlRepository:
    """Store and retrieve canonical rendered HTML by its uncompressed identity."""

    def __init__(self, store: ObjectStore, *, compression_level: int = 6) -> None:
        self.store = store
        self.compression_level = compression_level

    def identify(self, captured_html: str, *, chunk_chars: int = 1_048_576) -> HtmlIdentity:
        """Hash UTF-8 canonical bytes without allocating one complete byte copy."""

        return identify_html(captured_html, chunk_chars=chunk_chars)

    def put(
        self,
        captured_html: str,
        *,
        source_url: str,
        visit_id: UUID,
        observed_at: datetime,
        content_type: str,
        identity: HtmlIdentity | None = None,
        chunk_chars: int = 1_048_576,
    ) -> StoredHtml:
        identity = identity or self.identify(captured_html, chunk_chars=chunk_chars)
        key = identity.object_key
        if self.store.exists(key):
            self.verify(key, expected=identity)
            return StoredHtml(
                sha256=identity.sha256,
                object_key=key,
                size_bytes=identity.size_bytes,
                compressed_size_bytes=self.store.size(key),
                created=False,
            )

        with tempfile.TemporaryFile(mode="w+b") as compressed:
            compressor = zstandard.ZstdCompressor(level=self.compression_level)
            with compressor.stream_writer(compressed, closefd=False) as writer:
                for offset in range(0, len(captured_html), chunk_chars):
                    writer.write(
                        captured_html[offset : offset + chunk_chars].encode("utf-8")
                    )
            compressed_size = compressed.tell()
            compressed.seek(0)
            created = self.store.put_if_absent(
                key,
                compressed,
                headers=ObjectWriteHeaders(
                    content_type=f"{content_type}; charset=utf-8",
                    content_encoding="zstd",
                    metadata={
                        "url": source_url,
                        "visit-id": str(visit_id),
                        "observed-at": observed_at.isoformat(),
                    },
                ),
            )
        return StoredHtml(
            sha256=identity.sha256,
            object_key=key,
            size_bytes=identity.size_bytes,
            compressed_size_bytes=(
                compressed_size if created else self.store.size(key)
            ),
            created=created,
        )

    def read(self, object_key: str) -> str:
        return self.read_bytes(object_key).decode("utf-8")

    def read_bytes(self, object_key: str, *, chunk_bytes: int = 1024 * 1024) -> bytes:
        """Return the exact canonical UTF-8 bytes after verifying their identity."""

        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be greater than zero")
        chunks: list[bytes] = []
        digest = hashlib.sha256()
        with self.store.open(object_key) as content:
            decompressor = zstandard.ZstdDecompressor()
            with decompressor.stream_reader(content) as reader:
                while chunk := reader.read(chunk_bytes):
                    digest.update(chunk)
                    chunks.append(chunk)
        expected = _sha256_from_key(object_key)
        if expected is not None and digest.hexdigest() != expected:
            raise RepositoryIntegrityError(
                f"HTML object failed content-address verification: {object_key}"
            )
        return b"".join(chunks)

    def verify(
        self,
        object_key: str,
        *,
        expected: HtmlIdentity | None = None,
        chunk_bytes: int = 1024 * 1024,
    ) -> HtmlIdentity:
        """Stream and verify one compressed object without materializing its HTML."""

        if chunk_bytes <= 0:
            raise ValueError("chunk_bytes must be greater than zero")
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with self.store.open(object_key) as content:
                with zstandard.ZstdDecompressor().stream_reader(content) as reader:
                    while chunk := reader.read(chunk_bytes):
                        digest.update(chunk)
                        size_bytes += len(chunk)
        except RepositoryIntegrityError:
            raise
        except Exception as exc:
            raise RepositoryIntegrityError(
                f"HTML object could not be verified: {object_key}"
            ) from exc

        identity = HtmlIdentity(sha256=digest.hexdigest(), size_bytes=size_bytes)
        key_sha256 = _sha256_from_key(object_key)
        if key_sha256 is not None and identity.sha256 != key_sha256:
            raise RepositoryIntegrityError(
                f"HTML object failed content-address verification: {object_key}"
            )
        if expected is not None and identity != expected:
            raise RepositoryIntegrityError(
                f"HTML object metadata does not match captured content: {object_key}"
            )
        return identity


def html_object_key(sha256: str) -> str:
    if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
        raise ValueError("HTML SHA-256 must be 64 lowercase hexadecimal characters")
    return f"raw/html/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}.html.zst"


def _sha256_from_key(object_key: str) -> str | None:
    filename = object_key.rsplit("/", 1)[-1]
    suffix = ".html.zst"
    candidate = filename[: -len(suffix)] if filename.endswith(suffix) else ""
    if len(candidate) == 64 and all(value in "0123456789abcdef" for value in candidate):
        return candidate
    return None
