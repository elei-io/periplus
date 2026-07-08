from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Index, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class Url(Base):
    __tablename__ = "urls"
    __table_args__ = (
        Index("ix_urls_host", "host"),
        Index("ix_urls_domain", "domain"),
        Index("ix_urls_path", "path"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    url: Mapped[str] = mapped_column(Text, unique=True)
    normalized_url: Mapped[str] = mapped_column(Text, unique=True)
    scheme: Mapped[str] = mapped_column(Text)
    host: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    query_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)

    crawls = relationship("Crawl", back_populates="url", foreign_keys="Crawl.url_id")
    artifacts = relationship("Artifact", back_populates="url", foreign_keys="Artifact.url_id")
