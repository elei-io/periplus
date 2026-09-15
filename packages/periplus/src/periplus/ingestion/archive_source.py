"""Immutable provenance for observations acquired by an external archive."""

from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field


class ArchiveSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    dataset: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    record_id: str = Field(min_length=1, max_length=1024)
    target_uri: str = Field(min_length=1, max_length=16384)
    filename: str = Field(min_length=1, max_length=2048)
    offset: int = Field(ge=0)
    length: int = Field(gt=0)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    warc_headers_base64: str = Field(max_length=131072)
    http_headers_base64: str = Field(max_length=131072)

    @property
    def capture_id(self) -> UUID:
        # A record's identity survives downloading it again or importing for
        # another collection. Location and import time are not observations.
        return uuid5(UUID("8c003376-359b-50fd-9124-ce3a6b02c041"),
                     f"{self.provider}:{self.dataset}:{self.record_id}")
