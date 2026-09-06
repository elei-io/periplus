"""Append random Common Crawl HTML pages to a disposable Periplus lake."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from dotenv import load_dotenv
import httpx

from periplus.urls import normalize_url


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE = ROOT / ".periplus" / "test-corpus"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_CRAWL = "CC-MAIN-2026-25"
DEFAULT_SEED = 20260727
DATASET_VERSION = "v4"
COMMON_CRAWL_DATA = "https://data.commoncrawl.org"
MINIMUM_SAMPLE_ROWS = 10_000
SAMPLE_MULTIPLIER = 3


@dataclass(frozen=True, slots=True)
class Capture:
    ordinal: int
    url: str
    status: int
    observed_at: str
    filename: str
    offset: int
    length: int
    declared_media_type: str | None
    charset: str | None

    @property
    def source_record_id(self) -> str:
        return (
            f"page:{self.ordinal}:{self.filename}:"
            f"{self.offset}:{self.length}"
        )


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Append random successful HTML pages from Common Crawl through "
            "Periplus's external ingestion API."
        )
    )
    parser.add_argument(
        "pages",
        type=positive_int,
        help="number of pages to add (for example: ./test_corpus 15000)",
    )
    parser.add_argument(
        "--crawl",
        default=DEFAULT_CRAWL,
        help=f"pinned Common Crawl collection (default: {DEFAULT_CRAWL})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"sampling seed (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERIPLUS_API_URL", DEFAULT_API_URL),
        help=f"Periplus API base URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="local directory holding downloaded index shards and selections",
    )
    parser.add_argument(
        "--jobs",
        type=positive_int,
        default=8,
        help="concurrent WARC downloads/API submissions (default: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "select and cache pages without querying Periplus, downloading WARC "
            "records, or changing the lake"
        ),
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def dataset_name(crawl: str, seed: int) -> str:
    return f"periplus-test-corpus/{DATASET_VERSION}/{crawl}/{seed}"


def manifest_path(cache_dir: Path, crawl: str, seed: int) -> Path:
    return cache_dir / f"{DATASET_VERSION}-{crawl}-{seed}.jsonl"


def load_manifest(path: Path) -> list[Capture]:
    if not path.exists():
        return []
    captures = [
        Capture(**json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    validate_manifest(captures)
    return captures


def save_manifest(path: Path, captures: Iterable[Capture]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        for capture in captures:
            output.write(json.dumps(asdict(capture), sort_keys=True) + "\n")
    temporary.replace(path)


def validate_manifest(captures: list[Capture]) -> None:
    ordinals = [capture.ordinal for capture in captures]
    if ordinals != sorted(set(ordinals)):
        raise ValueError("manifest ordinals must be unique and increasing")
    urls: set[str] = set()
    identities: set[tuple[str, int, int]] = set()
    for capture in captures:
        normalized = normalize_url(capture.url)
        identity = capture_identity(capture)
        if normalized in urls:
            raise ValueError(f"duplicate manifest URL: {normalized}")
        if identity in identities:
            raise ValueError(f"duplicate manifest capture: {identity}")
        urls.add(normalized)
        identities.add(identity)


def query_existing(api_url: str, dataset: str) -> set[int]:
    sql = f"""
        SELECT list(
                   try_cast(split_part(source_record_id, ':', 2) AS BIGINT)
                   ORDER BY try_cast(
                       split_part(source_record_id, ':', 2) AS BIGINT
                   )
               ) AS ordinals
        FROM web.observation
        WHERE source_kind = 'external'
          AND source_system = 'common-crawl'
          AND source_dataset = {sql_string(dataset)}
          AND starts_with(source_record_id, 'page:')
    """
    payload = query_periplus(sql)
    ordinals = payload["rows"][0][0] if payload["rows"] else None
    return {
        int(ordinal)
        for ordinal in (ordinals or [])
        if ordinal is not None
    }


def query_lake_urls(api_url: str, *, page_size: int = 10_000) -> set[str]:
    """Read existing normalized URLs in bounded pages for best-effort dedupe."""
    existing: set[str] = set()
    offset = 0
    while True:
        rows = query_periplus(
            "SELECT coalesce(effective_url, requested_url) AS url "
            "FROM web.observation GROUP BY url "
            f"ORDER BY url LIMIT {page_size} OFFSET {offset}",
        )["rows"]
        existing.update(str(row[0]) for row in rows)
        if len(rows) < page_size:
            return existing
        offset += page_size


def query_periplus(sql: str) -> dict[str, Any]:
    api_url = os.environ.get("PERIPLUS_QUERY_URL", "http://127.0.0.1:8010")
    token = os.environ["PERIPLUS_QUERY_API_TOKEN"]
    with httpx.Client(timeout=60) as client:
        response = client.post(
            f"{api_url.rstrip('/')}/query/exec",
            headers={"Authorization": f"Bearer {token}"},
            json={"sql": sql},
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload


def pending_captures(
    captures: list[Capture],
    existing_ordinals: set[int],
    count: int,
    *,
    existing_urls: set[str] | None = None,
) -> list[Capture]:
    duplicate_urls = existing_urls or set()
    return [
        capture
        for capture in captures
        if capture.ordinal not in existing_ordinals
        and normalize_url(capture.url) not in duplicate_urls
    ][:count]


def wait_for_ingestion(
    api_url: str,
    dataset: str,
    selected: list[Capture],
    *,
    timeout_seconds: float = 900,
    poll_seconds: float = 1,
) -> None:
    expected = {capture.ordinal for capture in selected}
    deadline = time.monotonic() + timeout_seconds
    previous_remaining: int | None = None
    while True:
        remaining = len(expected - query_existing(api_url, dataset))
        if remaining != previous_remaining:
            report(f"phase=ingestion remaining={remaining}")
            previous_remaining = remaining
        if remaining == 0:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"timed out waiting for {remaining} corpus observations "
                "to finish ingestion"
            )
        time.sleep(poll_seconds)


def select_captures(
    *,
    captures: list[Capture],
    existing_ordinals: set[int],
    existing_urls: set[str],
    count: int,
    crawl: str,
    seed: int,
    cache_dir: Path,
) -> list[Capture]:
    pending = pending_captures(
        captures,
        existing_ordinals,
        count,
        existing_urls=existing_urls,
    )
    if len(pending) == count:
        report(f"select pages: {count}/{count} cached")
        return captures
    selected_count = len(pending)

    excluded_urls = set(existing_urls)
    excluded_urls.update(normalize_url(capture.url) for capture in captures)
    excluded_captures = {capture_identity(capture) for capture in captures}
    next_ordinal = max(
        existing_ordinals | {capture.ordinal for capture in captures},
        default=-1,
    ) + 1
    paths = sorted(
        load_index_paths(crawl=crawl, cache_dir=cache_dir),
        key=lambda path: stable_key(seed, path),
    )
    for shard_number, remote_path in enumerate(paths, start=1):
        missing = count - selected_count
        if missing <= 0:
            report(f"select pages: {count}/{count} complete")
            return captures
        report(
            f"select pages: shard {shard_number}/{len(paths)}; "
            f"remaining={missing}"
        )
        local_path = download_index_shard(
            remote_path=remote_path,
            cache_dir=cache_dir,
        )
        candidates = sample_index_shard(
            path=local_path,
            seed=seed + shard_number - 1,
            sample_rows=max(MINIMUM_SAMPLE_ROWS, missing * SAMPLE_MULTIPLIER),
        )
        candidates.sort(
            key=lambda item: stable_key(
                seed,
                str(item["url"]),
                str(item["filename"]),
                str(item["offset"]),
            )
        )
        for item in candidates:
            try:
                url = normalize_url(str(item["url"]))
                identity = candidate_identity(item)
            except (TypeError, ValueError):
                continue
            if url in excluded_urls or identity in excluded_captures:
                continue
            captures.append(capture_from_index(next_ordinal, item, url=url))
            next_ordinal += 1
            selected_count += 1
            excluded_urls.add(url)
            excluded_captures.add(identity)
            if selected_count == count:
                report(f"select pages: {count}/{count} complete")
                return captures
    raise RuntimeError(
        f"Common Crawl's HTML index yielded fewer than {count} new URLs"
    )


def load_index_paths(*, crawl: str, cache_dir: Path) -> list[str]:
    index_dir = cache_dir / "index" / crawl
    path = index_dir / "cc-index-table.paths"
    if not path.exists():
        report("select pages: downloading index shard list")
        compressed = download_bytes(
            f"{COMMON_CRAWL_DATA}/crawl-data/{crawl}/"
            "cc-index-table.paths.gz"
        )
        index_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(gzip.decompress(compressed))
        temporary.replace(path)
    paths = [
        line.strip()
        for line in path.read_text().splitlines()
        if "/subset=warc/" in line
    ]
    if not paths:
        raise RuntimeError(
            f"Common Crawl published no WARC URL-index shards for {crawl}"
        )
    return paths


def download_bytes(url: str, *, timeout: float = 120) -> bytes:
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = httpx.get(url, timeout=timeout, follow_redirects=True)
            response.raise_for_status()
            return response.content
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt == 4:
                break
            delay = 0.5 * (2**attempt)
            report(f"download failed; retrying in {delay:g}s")
            time.sleep(delay)
    raise RuntimeError(f"failed to download {url}") from last_error


def download_index_shard(*, remote_path: str, cache_dir: Path) -> Path:
    crawl = remote_path.split("/crawl=", 1)[1].split("/", 1)[0]
    local_dir = cache_dir / "index" / crawl / "warc"
    local_path = local_dir / Path(remote_path).name
    if local_path.exists():
        report(f"select pages: cached {local_path.name}")
        return local_path
    local_dir.mkdir(parents=True, exist_ok=True)
    temporary = local_path.with_suffix(local_path.suffix + ".partial")
    report(f"select pages: downloading {local_path.name}")
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with httpx.stream(
                "GET",
                f"{COMMON_CRAWL_DATA}/{remote_path}",
                timeout=120,
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            temporary.replace(local_path)
            return local_path
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt == 4:
                break
            delay = 0.5 * (2**attempt)
            report(f"index shard download failed; retrying in {delay:g}s")
            time.sleep(delay)
    raise RuntimeError(f"failed to download URL-index shard {remote_path}") from (
        last_error
    )


def sample_index_shard(
    *,
    path: Path,
    seed: int,
    sample_rows: int,
) -> list[dict[str, Any]]:
    import duckdb

    sql = f"""
        SELECT url,
               fetch_time,
               fetch_status AS status,
               content_mime_type AS mime,
               content_charset AS encoding,
               warc_filename AS filename,
               warc_record_offset AS offset,
               warc_record_length AS length
        FROM read_parquet(?)
        WHERE fetch_status = 200
          AND content_mime_detected = 'text/html'
          AND url_protocol IN ('http', 'https')
          AND length(url) <= 2048
          AND warc_filename IS NOT NULL
          AND warc_record_offset IS NOT NULL
          AND warc_record_length IS NOT NULL
        USING SAMPLE 10 PERCENT (system, {seed})
        LIMIT {sample_rows}
    """
    with duckdb.connect() as connection:
        result = connection.execute(sql, [str(path)])
        columns = [description[0] for description in result.description]
        rows = result.fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def capture_from_index(
    ordinal: int,
    item: dict[str, Any],
    *,
    url: str,
) -> Capture:
    observed_at = item["fetch_time"]
    if not isinstance(observed_at, datetime):
        raise ValueError("Common Crawl fetch_time is not a timestamp")
    return Capture(
        ordinal=ordinal,
        url=url,
        status=int(item["status"]),
        observed_at=observed_at.astimezone(UTC).isoformat(),
        filename=str(item["filename"]),
        offset=int(item["offset"]),
        length=int(item["length"]),
        declared_media_type=(str(item["mime"]) if item.get("mime") else None),
        charset=str(item["encoding"]) if item.get("encoding") else None,
    )


def candidate_identity(item: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(item["filename"]),
        int(item["offset"]),
        int(item["length"]),
    )


def capture_identity(capture: Capture) -> tuple[str, int, int]:
    return (capture.filename, capture.offset, capture.length)


def stable_key(seed: int, *values: str) -> bytes:
    digest = hashlib.sha256()
    digest.update(str(seed).encode())
    for value in values:
        digest.update(b"\0")
        digest.update(value.encode())
    return digest.digest()


def ingest_capture(
    capture: Capture,
    *,
    api_url: str,
    dataset: str,
    client: httpx.Client,
) -> str:
    end = capture.offset + capture.length - 1
    headers = {"Range": f"bytes={capture.offset}-{end}"}
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = client.get(
                f"{COMMON_CRAWL_DATA}/{capture.filename}",
                headers=headers,
            )
            response.raise_for_status()
            content, media_type, charset = extract_html(response.content)
            if not content:
                raise ValueError("WARC response contained an empty body")
            metadata = {
                "source_record_id": capture.source_record_id,
                "system": "common-crawl",
                "dataset": dataset,
                "requested_url": capture.url,
                "effective_url": capture.url,
                "observed_at": capture.observed_at,
                "status_code": capture.status,
                "declared_media_type": (
                    capture.declared_media_type or media_type or "text/html"
                ),
                "charset": capture.charset or charset,
            }
            if metadata["charset"] is None:
                metadata.pop("charset")
            imported = client.post(
                f"{api_url.rstrip('/')}/ingest/html",
                data={"metadata": json.dumps(metadata)},
                files={"content": ("document.html", content, "text/html")},
            )
            imported.raise_for_status()
            return str(imported.json()["disposition"])
        except (httpx.HTTPError, OSError, ValueError) as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(
        f"failed to ingest page:{capture.ordinal} {capture.url}"
    ) from last_error


def extract_html(compressed_record: bytes) -> tuple[bytes, str | None, str | None]:
    from warcio.archiveiterator import ArchiveIterator

    records = ArchiveIterator(io.BytesIO(compressed_record), arc2warc=True)
    record = next(records, None)
    if record is None or record.rec_type not in {"response", "revisit"}:
        raise ValueError("WARC range did not contain an HTTP response")
    if record.http_headers is None:
        raise ValueError("WARC response did not contain HTTP headers")
    content_type = record.http_headers.get_header("Content-Type")
    media_type: str | None = None
    charset: str | None = None
    if content_type:
        parts = [part.strip() for part in content_type.split(";")]
        media_type = parts[0] or None
        for part in parts[1:]:
            name, separator, value = part.partition("=")
            if separator and name.strip().lower() == "charset":
                charset = value.strip().strip("\"'") or None
    return record.content_stream().read(), media_type, charset


def report(message: str) -> None:
    print(message, flush=True)


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def progress_interval(total: int) -> int:
    return max(1, total // 20)


def reconcile(arguments: argparse.Namespace) -> int:
    dataset = dataset_name(arguments.crawl, arguments.seed)
    path = manifest_path(
        arguments.cache_dir.resolve(),
        arguments.crawl,
        arguments.seed,
    )
    captures = load_manifest(path)
    if arguments.dry_run:
        existing_ordinals: set[int] = set()
        existing_urls: set[str] = set()
        report(f"dataset={dataset} add={arguments.pages} lake-dedupe=skipped")
    else:
        report("phase=lake-scan")
        existing_ordinals = query_existing(arguments.api_url, dataset)
        existing_urls = query_lake_urls(arguments.api_url)
        report(
            f"dataset={dataset} add={arguments.pages} "
            f"existing-pages={len(existing_urls)}"
        )

    report("phase=selection")
    captures = select_captures(
        captures=captures,
        existing_ordinals=existing_ordinals,
        existing_urls=existing_urls,
        count=arguments.pages,
        crawl=arguments.crawl,
        seed=arguments.seed,
        cache_dir=arguments.cache_dir.resolve(),
    )
    save_manifest(path, captures)
    selected = pending_captures(
        captures,
        existing_ordinals,
        arguments.pages,
        existing_urls=existing_urls,
    )
    report(f"selection complete: selected={len(selected)} cached={len(captures)}")
    if arguments.dry_run:
        report(
            f"dry-run: manifest cached at {path}; no Periplus data changed"
        )
        return 0

    report(f"phase=submission pages={len(selected)} concurrency={arguments.jobs}")
    completed = 0
    dispositions: dict[str, int] = {}
    failures: list[tuple[Capture, Exception]] = []
    successful: list[Capture] = []
    interval = progress_interval(len(selected))
    limits = httpx.Limits(
        max_connections=arguments.jobs,
        max_keepalive_connections=arguments.jobs,
    )
    with httpx.Client(
        timeout=120,
        follow_redirects=True,
        limits=limits,
    ) as client:
        with ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
            futures = {
                executor.submit(
                    ingest_capture,
                    capture,
                    api_url=arguments.api_url,
                    dataset=dataset,
                    client=client,
                ): capture
                for capture in selected
            }
            for future in as_completed(futures):
                capture = futures[future]
                try:
                    disposition = future.result()
                except Exception as exc:
                    failures.append((capture, exc))
                else:
                    dispositions[disposition] = (
                        dispositions.get(disposition, 0) + 1
                    )
                    successful.append(capture)
                completed += 1
                if completed % interval == 0 or completed == len(selected):
                    report(
                        f"submitted={completed}/{len(selected)} "
                        f"failed={len(failures)}"
                    )
    if dispositions:
        report(
            "submission="
            + ", ".join(
                f"{name}:{count}"
                for name, count in sorted(dispositions.items())
            )
        )
    if successful:
        wait_for_ingestion(arguments.api_url, dataset, successful)
    if failures:
        for capture, error in failures[:20]:
            report(f"failed page:{capture.ordinal} {capture.url}: {error}")
        if len(failures) > 20:
            report(f"failed: {len(failures) - 20} more pages omitted")
        report(
            f"Periplus committed {len(successful)} pages; rerun the command to "
            f"retry the {len(failures)} failures."
        )
        return 1
    report(f"Periplus committed {len(successful)} new pages.")
    return 0


def main() -> None:
    try:
        load_dotenv(ROOT / ".env")
        raise SystemExit(reconcile(parse_arguments()))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"test_corpus: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
