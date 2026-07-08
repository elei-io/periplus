from __future__ import annotations

from hashlib import sha256
from urllib.parse import parse_qsl, urlencode, urldefrag, urlparse, urlunparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Url


def normalize_url(value: str) -> str:
    url, _ = urldefrag(value.strip())
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def query_fingerprint(value: str) -> str | None:
    parsed = urlparse(value)
    if not parsed.query:
        return None

    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return sha256(query.encode()).hexdigest()


def resolve_url(session: Session, value: str) -> Url:
    normalized = normalize_url(value)
    existing = session.scalar(select(Url).where(Url.normalized_url == normalized))
    if existing is not None:
        return existing

    parsed = urlparse(normalized)
    url = Url(
        url=value,
        normalized_url=normalized,
        scheme=parsed.scheme,
        host=parsed.netloc,
        domain=parsed.netloc.removeprefix("www."),
        path=parsed.path or "/",
        query_fingerprint=query_fingerprint(normalized),
    )
    session.add(url)
    session.flush()
    return url
