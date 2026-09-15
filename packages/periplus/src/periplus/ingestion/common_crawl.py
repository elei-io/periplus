"""Bounded Common Crawl selection and verified WARC response decoding.

Only complete, successful UTF-8 HTML responses with identity HTTP encodings
are admitted. Unsupported records fail explicitly rather than becoming empty
or incorrectly decoded observations. Remote requests only target CC endpoints.
"""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
import gzip
from hashlib import sha256
from io import BytesIO
import json
import re
import time
from threading import Lock

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from warcio.archiveiterator import ArchiveIterator

from periplus.ingestion.archive_source import ArchiveSource
from periplus.urls import normalize_url

MAX_RECORD_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 32 * 1024 * 1024
_lookup_lock = Lock()
_next_lookup = 0.0


class UnsupportedCapture(ValueError):
    """Valid archive evidence outside the importer's supported representation."""


class CommonCrawlRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    dataset: str = Field(pattern=r"^CC-MAIN-[0-9]{4}-[0-9]{2}$")
    url: str
    timestamp: str = Field(pattern=r"^[0-9]{14}$")
    filename: str
    offset: int = Field(ge=0)
    length: int = Field(gt=0, le=MAX_RECORD_BYTES)

    @field_validator("filename")
    @classmethod
    def archive_path(cls, value: str) -> str:
        if not re.fullmatch(r"crawl-data/CC-MAIN-[0-9]{4}-[0-9]{2}/segments/[A-Za-z0-9./_-]+\.warc\.gz", value):
            raise ValueError("invalid Common Crawl WARC path")
        if ".." in value.split("/"):
            raise ValueError("invalid Common Crawl WARC path")
        return value

    @property
    def captured_at(self) -> datetime:
        return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


@dataclass(frozen=True)
class DecodedCapture:
    source: ArchiveSource
    captured_at: datetime
    url: str
    html: str
    content_type: str


def decode_record(item: CommonCrawlRecord, data: bytes) -> DecodedCapture:
    if len(data) != item.length:
        raise ValueError("WARC range length differs from index")
    with gzip.GzipFile(fileobj=BytesIO(data)) as compressed:
        expanded = compressed.read(MAX_EXPANDED_BYTES + 1)
    if len(expanded) > MAX_EXPANDED_BYTES:
        raise ValueError("expanded WARC exceeds byte limit")
    boundary = expanded.find(b"\r\n\r\n") + 4
    if boundary < 4 or boundary > 65536:
        raise ValueError("invalid or oversized WARC header block")
    iterator = ArchiveIterator(BytesIO(expanded), check_digests=True)
    record = next(iterator)
    if record.rec_type != "response" or record.http_headers is None:
        raise UnsupportedCapture("only complete WARC response records are supported; revisit resolution is not enabled")
    if record.rec_headers.get_header("WARC-Truncated") or record.rec_headers.get_header("WARC-Segment-Number"):
        raise UnsupportedCapture("truncated or segmented captures cannot fulfill imports")
    body = record.raw_stream.read(MAX_EXPANDED_BYTES + 1)
    if next(iterator, None) is not None or record.digest_checker.problems:
        raise ValueError("WARC digest mismatch or range contains multiple records")
    if record.http_headers.get_statuscode() != "200":
        raise UnsupportedCapture("only successful HTTP 200 HTML is supported")
    for name in ("Transfer-Encoding", "Content-Encoding"):
        if (record.http_headers.get_header(name) or "identity").lower() != "identity":
            raise UnsupportedCapture(f"unsupported HTTP {name}; exact bytes were not imported")
    headers = Message()
    content_type = record.http_headers.get_header("Content-Type") or ""
    headers["Content-Type"] = content_type
    if headers.get_content_type() != "text/html":
        raise UnsupportedCapture("only text/html responses are supported")
    if (headers.get_content_charset() or "utf-8").lower() not in {"utf-8", "utf8", "us-ascii"}:
        raise UnsupportedCapture("only UTF-8 HTML is supported")
    try:
        html = body.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise UnsupportedCapture("only valid UTF-8 HTML is supported") from error
    target = record.rec_headers.get_header("WARC-Target-URI")
    observed = datetime.fromisoformat(record.rec_headers.get_header("WARC-Date"))
    if observed.utcoffset() is None or observed != item.captured_at:
        raise ValueError("WARC capture time differs from index")
    if normalize_url(target) != normalize_url(item.url):
        raise ValueError("WARC target differs from index URL")
    block_length = int(record.rec_headers.get_header("Content-Length"))
    block = expanded[boundary:boundary + block_length]
    http_end = block.find(b"\r\n\r\n") + 4
    if http_end < 4 or http_end > 65536 or block[http_end:] != body:
        raise ValueError("HTTP block does not match retained payload")
    source = ArchiveSource(provider="common-crawl", dataset=item.dataset,
        record_id=record.rec_headers.get_header("WARC-Record-ID"), target_uri=target,
        filename=item.filename, offset=item.offset, length=item.length,
        record_sha256=sha256(data).hexdigest(),
        warc_headers_base64=base64.b64encode(expanded[:boundary]).decode(),
        http_headers_base64=base64.b64encode(block[:http_end]).decode())
    return DecodedCapture(source, observed, normalize_url(target), html, content_type)


class CommonCrawlClient:
    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=30, follow_redirects=False, trust_env=False,
                                          headers={"User-Agent": "Periplus archive importer/1"})

    def close(self) -> None:
        self.http.close()

    def lookup(self, url: str, dataset: str, *, since: datetime, until: datetime) -> CommonCrawlRecord | None:
        global _next_lookup
        if not re.fullmatch(r"CC-MAIN-[0-9]{4}-[0-9]{2}", dataset):
            raise ValueError("invalid CC dataset")
        url = normalize_url(url)
        with _lookup_lock:
            time.sleep(max(0, _next_lookup - time.monotonic()))
            _next_lookup = time.monotonic() + 1
        params = [("url", url), ("matchType", "exact"), ("output", "json"),
                  ("filter", "=status:200"), ("filter", "=mime:text/html"),
                  ("from", since.strftime("%Y%m%d%H%M%S")), ("to", until.strftime("%Y%m%d%H%M%S")),
                  ("limit", "20"), ("sort", "reverse")]
        with self.http.stream("GET", f"https://index.commoncrawl.org/{dataset}-index", params=params) as response:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = _bounded_response(response, 256 * 1024)
        candidates = [CommonCrawlRecord.model_validate({**json.loads(line), "dataset": dataset})
                      for line in data.splitlines() if line.strip()]
        # CDX exact matching uses URL canonicalization. Never reuse HTTP as HTTPS,
        # or another URL that the provider's canonicalization considers equivalent.
        candidates = [x for x in candidates if normalize_url(x.url) == url and since <= x.captured_at <= until]
        return max(candidates, key=lambda x: x.timestamp) if candidates else None

    def fetch(self, item: CommonCrawlRecord) -> bytes:
        end = item.offset + item.length - 1
        with self.http.stream("GET", "https://data.commoncrawl.org/" + item.filename,
                              headers={"Range": f"bytes={item.offset}-{end}", "Accept-Encoding": "identity"}) as response:
            response.raise_for_status()
            if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {item.offset}-{end}/"):
                raise ValueError("archive server did not honor the exact byte range")
            return _bounded_response(response, item.length)


def _bounded_response(response: httpx.Response, limit: int) -> bytes:
    data = bytearray()
    for chunk in response.iter_bytes(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("remote response exceeds byte limit")
    return bytes(data)
