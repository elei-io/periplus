"""Reconcile a deterministic Common Crawl corpus into a disposable Atlas lake."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Literal

from dotenv import load_dotenv
import httpx


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KNOWN_DOMAINS = (
    ROOT / "backend" / "tests" / "corpus" / "known_domains.txt"
)
DEFAULT_CACHE = ROOT / ".atlas" / "test-corpus"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_CRAWL = "CC-MAIN-2026-25"
DEFAULT_SEED = 20260727
DATASET_VERSION = "v3"
COMMON_CRAWL_DATA = "https://data.commoncrawl.org"
COMMON_CRAWL_INDEX = "https://index.commoncrawl.org"
KNOWN_CAPTURES_PER_DOMAIN = 1_000
DEFAULT_NOISE_INDEX_SHARDS = 3
MINIMUM_NOISE_SAMPLE_ROWS = 50_000
NOISE_SAMPLE_MULTIPLIER = 5
NOISE_CAPTURES_PER_DOMAIN = 20

Tier = Literal["known", "noise", "failure"]


@dataclass(frozen=True, slots=True)
class Capture:
    tier: Tier
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
            f"{self.tier}:{self.ordinal}:{self.filename}:"
            f"{self.offset}:{self.length}"
        )


@dataclass(frozen=True, slots=True)
class Targets:
    known: int
    noise: int
    failure: int

    def for_tier(self, tier: Tier) -> int:
        return int(getattr(self, tier))

    @property
    def total(self) -> int:
        return self.known + self.noise + self.failure


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile a deterministic Common Crawl sample through Atlas's "
            "external HTML ingestion API."
        )
    )
    parser.add_argument(
        "--known",
        "--known-pages",
        dest="known",
        type=non_negative_int,
        default=0,
        help="number of HTTP 200 pages selected from known domains",
    )
    parser.add_argument(
        "--noise",
        "--noise-pages",
        dest="noise",
        type=non_negative_int,
        default=0,
        help="number of arbitrary internet pages, including the failure share",
    )
    parser.add_argument(
        "--fail",
        "--failure-rate",
        dest="failure_rate",
        type=failure_rate,
        default=0.0,
        help=(
            "share of the combined corpus whose stored HTTP status is not 200; "
            "failure pages are drawn from --noise"
        ),
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
        help=f"deterministic sampling seed (default: {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ATLAS_API_URL", DEFAULT_API_URL),
        help=f"Atlas API base URL (default: {DEFAULT_API_URL})",
    )
    parser.add_argument(
        "--known-domains",
        type=Path,
        default=DEFAULT_KNOWN_DOMAINS,
        help="newline-delimited known-domain list",
    )
    parser.add_argument(
        "--noise-index-shards",
        type=positive_int,
        default=DEFAULT_NOISE_INDEX_SHARDS,
        help=(
            "maximum deterministic Common Crawl Parquet shards to sample "
            f"(default: {DEFAULT_NOISE_INDEX_SHARDS})"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE,
        help="local directory holding the stable selection manifest",
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
            "select and cache the complete manifest without changing Atlas or "
            "downloading WARC records"
        ),
    )
    arguments = parser.parse_args(argv)
    if arguments.failure_rate > 0 and arguments.noise == 0:
        parser.error("--fail requires --noise to be greater than zero")
    try:
        targets_for(arguments.known, arguments.noise, arguments.failure_rate)
    except ValueError as exc:
        parser.error(str(exc))
    return arguments


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def failure_rate(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def targets_for(known: int, noise: int, rate: float) -> Targets:
    failed = math.floor((known + noise) * rate + 0.5)
    if failed > noise:
        raise ValueError(
            "--fail is too large to draw entirely from the --noise pages"
        )
    return Targets(known=known, noise=noise - failed, failure=failed)


def dataset_name(crawl: str, seed: int) -> str:
    return f"atlas-test-corpus/{DATASET_VERSION}/{crawl}/{seed}"


def load_domains(path: Path) -> list[str]:
    domains = [
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not domains:
        raise ValueError(f"domain list is empty: {path}")
    if any("/" in domain or ":" in domain for domain in domains):
        raise ValueError("domain entries must be bare domain names")
    return list(dict.fromkeys(domains))


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
    seen: set[tuple[Tier, int]] = set()
    next_ordinal: dict[Tier, int] = {"known": 0, "noise": 0, "failure": 0}
    for capture in captures:
        key = (capture.tier, capture.ordinal)
        if key in seen:
            raise ValueError(f"duplicate manifest identity: {key}")
        if capture.ordinal != next_ordinal[capture.tier]:
            raise ValueError(
                f"non-contiguous {capture.tier} manifest ordinal "
                f"{capture.ordinal}; expected {next_ordinal[capture.tier]}"
            )
        seen.add(key)
        next_ordinal[capture.tier] += 1


def captures_by_tier(captures: list[Capture]) -> dict[Tier, list[Capture]]:
    grouped: dict[Tier, list[Capture]] = {
        "known": [],
        "noise": [],
        "failure": [],
    }
    for capture in captures:
        grouped[capture.tier].append(capture)
    return grouped


def manifest_path(cache_dir: Path, crawl: str, seed: int) -> Path:
    return cache_dir / f"{DATASET_VERSION}-{crawl}-{seed}.jsonl"


def query_existing(api_url: str, dataset: str) -> dict[Tier, set[int]]:
    quoted = sql_string(dataset)
    sql = f"""
        SELECT split_part(provenance.source_record_id::VARCHAR, ':', 1) AS tier,
               list(
                   try_cast(
                       split_part(
                           provenance.source_record_id::VARCHAR, ':', 2
                       ) AS BIGINT
                   )
                   ORDER BY try_cast(
                       split_part(
                           provenance.source_record_id::VARCHAR, ':', 2
                       ) AS BIGINT
                   )
               ) AS ordinals
        FROM ingest.visits
        WHERE provenance.kind::VARCHAR = 'external'
          AND provenance.system::VARCHAR = 'common-crawl'
          AND provenance.dataset::VARCHAR = {quoted}
        GROUP BY tier
    """
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{api_url.rstrip('/')}/sql/query",
            json={"sql": sql},
        )
        response.raise_for_status()
        payload = response.json()
    existing: dict[Tier, set[int]] = {
        "known": set(),
        "noise": set(),
        "failure": set(),
    }
    for tier, ordinals in payload["rows"]:
        if tier in existing:
            existing[tier] = {
                int(ordinal) for ordinal in ordinals if ordinal is not None
            }
    return existing


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def report(message: str) -> None:
    print(message, flush=True)


def progress_interval(total: int) -> int:
    """Emit about twenty progress updates, including small corpora."""
    return max(1, total // 20)


def query_domain_candidates(
    *,
    label: str,
    domains: list[str],
    crawl: str,
    seed: int,
    status_filter: str,
    minimum_candidates: int,
    minimum_domains: int,
) -> list[dict[str, Any]]:
    report(f"select {label}: querying {len(domains)} domain indexes")
    candidates: dict[tuple[str, str, int], dict[str, Any]] = {}
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for domain_number, domain in enumerate(domains, start=1):
            report(
                f"select {label}: domain {domain_number}/{len(domains)} "
                f"{domain}; candidates={len(candidates)}"
            )
            try:
                response = get_cdx_response(
                    client,
                    crawl=crawl,
                    domain=domain,
                    status_filter=status_filter,
                )
            except httpx.HTTPError as exc:
                report(
                    f"select {label}: skipping {domain} after retries: {exc}"
                )
                continue
            if is_empty_cdx_result(response):
                continue
            response.raise_for_status()
            for line in response.text.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                key = (
                    str(item["filename"]),
                    str(item["offset"]),
                    int(item["length"]),
                )
                candidates[key] = item
            if (
                domain_number >= minimum_domains
                and len(candidates) >= minimum_candidates
            ):
                report(
                    f"select {label}: enough candidates after "
                    f"{domain_number}/{len(domains)} domains; "
                    f"candidates={len(candidates)}"
                )
                break
    return sorted(
        candidates.values(),
        key=lambda item: stable_key(
            seed,
            str(item["url"]),
            str(item["filename"]),
            str(item["offset"]),
        ),
    )


def query_noise_index_candidates(
    *,
    crawl: str,
    seed: int,
    cache_dir: Path,
    known_domains: list[str],
    noise_target: int,
    failure_target: int,
    shard_limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    noise_paths = load_noise_index_paths(
        crawl=crawl,
        cache_dir=cache_dir,
        subset="warc",
    )
    failure_paths = load_noise_index_paths(
        crawl=crawl,
        cache_dir=cache_dir,
        subset="crawldiagnostics",
    )
    selected_noise_paths = sorted(
        noise_paths,
        key=lambda path: stable_key(seed, path),
    )[:shard_limit]
    selected_failure_paths = sorted(
        failure_paths,
        key=lambda path: stable_key(seed, path),
    )[:shard_limit]
    sample_rows = max(
        MINIMUM_NOISE_SAMPLE_ROWS,
        (noise_target + failure_target) * NOISE_SAMPLE_MULTIPLIER,
    )
    noise: dict[tuple[str, str, int], dict[str, Any]] = {}
    failure: dict[tuple[str, str, int], dict[str, Any]] = {}
    noise_domain_counts: dict[str, int] = {}
    failure_domain_counts: dict[str, int] = {}
    for shard_number, remote_path in enumerate(selected_noise_paths, start=1):
        report(
            f"select noise index: success shard {shard_number}/{shard_limit}; "
            f"noise={len(noise)}/{noise_target}"
        )
        local_path = download_noise_index_shard(
            remote_path=remote_path,
            cache_dir=cache_dir,
        )
        for item in sample_noise_index_shard(
            path=local_path,
            seed=seed + shard_number - 1,
            sample_rows=sample_rows,
        ):
            hostname = str(item["hostname"]).lower()
            if domain_is_known(hostname, known_domains):
                continue
            registered_domain = str(item["registered_domain"] or hostname)
            if (
                noise_domain_counts.get(registered_domain, 0)
                >= NOISE_CAPTURES_PER_DOMAIN
            ):
                continue
            if int(item["status"]) != 200:
                continue
            key = (
                str(item["filename"]),
                str(item["offset"]),
                int(item["length"]),
            )
            if key in noise:
                continue
            noise[key] = item
            noise_domain_counts[registered_domain] = (
                noise_domain_counts.get(registered_domain, 0) + 1
            )
        if len(noise) >= noise_target:
            break
    for shard_number, remote_path in enumerate(
        selected_failure_paths,
        start=1,
    ):
        report(
            f"select noise index: failure shard {shard_number}/{shard_limit}; "
            f"failure={len(failure)}/{failure_target}"
        )
        local_path = download_noise_index_shard(
            remote_path=remote_path,
            cache_dir=cache_dir,
        )
        for item in sample_noise_index_shard(
            path=local_path,
            seed=seed + shard_number - 1,
            sample_rows=sample_rows,
        ):
            hostname = str(item["hostname"]).lower()
            if domain_is_known(hostname, known_domains):
                continue
            registered_domain = str(item["registered_domain"] or hostname)
            if (
                failure_domain_counts.get(registered_domain, 0)
                >= NOISE_CAPTURES_PER_DOMAIN
            ):
                continue
            if int(item["status"]) == 200:
                continue
            key = (
                str(item["filename"]),
                str(item["offset"]),
                int(item["length"]),
            )
            if key in failure:
                continue
            failure[key] = item
            failure_domain_counts[registered_domain] = (
                failure_domain_counts.get(registered_domain, 0) + 1
            )
        if len(failure) >= failure_target:
            break
    return (
        sorted(
            noise.values(),
            key=lambda item: stable_key(
                seed,
                str(item["url"]),
                str(item["filename"]),
                str(item["offset"]),
            ),
        ),
        sorted(
            failure.values(),
            key=lambda item: stable_key(
                seed,
                str(item["url"]),
                str(item["filename"]),
                str(item["offset"]),
            ),
        ),
    )


def load_noise_index_paths(
    *,
    crawl: str,
    cache_dir: Path,
    subset: str,
) -> list[str]:
    index_dir = cache_dir / "index" / crawl
    path = index_dir / "cc-index-table.paths"
    if not path.exists():
        report("select noise index: downloading shard list")
        response = httpx.get(
            f"{COMMON_CRAWL_DATA}/crawl-data/{crawl}/"
            "cc-index-table.paths.gz",
            timeout=120,
        )
        response.raise_for_status()
        index_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.decompress(response.content))
    paths = [
        line.strip()
        for line in path.read_text().splitlines()
        if f"/subset={subset}/" in line
    ]
    if not paths:
        raise RuntimeError(
            f"Common Crawl published no {subset} URL-index shards for {crawl}"
        )
    return paths


def download_noise_index_shard(*, remote_path: str, cache_dir: Path) -> Path:
    crawl = remote_path.split("/crawl=", 1)[1].split("/", 1)[0]
    subset = remote_path.split("/subset=", 1)[1].split("/", 1)[0]
    local_dir = cache_dir / "index" / crawl / subset
    local_path = local_dir / Path(remote_path).name
    if local_path.exists():
        report(f"select noise index: cached {local_path.name}")
        return local_path
    local_dir.mkdir(parents=True, exist_ok=True)
    temporary = local_path.with_suffix(local_path.suffix + ".partial")
    report(f"select noise index: downloading {local_path.name}")
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            with httpx.stream(
                "GET",
                f"{COMMON_CRAWL_DATA}/{remote_path}",
                timeout=120,
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
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"failed to download URL-index shard {remote_path}") from (
        last_error
    )


def sample_noise_index_shard(
    *,
    path: Path,
    seed: int,
    sample_rows: int,
) -> list[dict[str, Any]]:
    import duckdb

    sql = f"""
        SELECT url,
               url_host_name AS hostname,
               url_host_registered_domain AS registered_domain,
               fetch_time,
               fetch_status AS status,
               content_mime_type AS mime,
               content_charset AS encoding,
               warc_filename AS filename,
               warc_record_offset AS offset,
               warc_record_length AS length
        FROM read_parquet(?)
        WHERE content_mime_detected = 'text/html'
          AND fetch_status IS NOT NULL
          AND warc_filename IS NOT NULL
          AND warc_record_offset IS NOT NULL
          AND warc_record_length IS NOT NULL
        USING SAMPLE reservoir ({sample_rows} ROWS) REPEATABLE ({seed})
    """
    with duckdb.connect() as connection:
        columns = [
            description[0]
            for description in connection.execute(sql, [str(path)]).description
        ]
        rows = connection.fetchall()
    return [
        noise_index_row(dict(zip(columns, row, strict=True)))
        for row in rows
    ]


def noise_index_row(row: dict[str, Any]) -> dict[str, Any]:
    observed_at = row.pop("fetch_time")
    assert isinstance(observed_at, datetime)
    row["timestamp"] = observed_at.astimezone(UTC).strftime("%Y%m%d%H%M%S")
    return row


def domain_is_known(hostname: str, known_domains: list[str]) -> bool:
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in known_domains
    )


def get_cdx_response(
    client: httpx.Client,
    *,
    crawl: str,
    domain: str,
    status_filter: str,
) -> httpx.Response:
    last_error: httpx.HTTPError | None = None
    for attempt in range(5):
        try:
            response = client.get(
                f"{COMMON_CRAWL_INDEX}/{crawl}-index",
                params={
                    "url": f"{domain}/*",
                    "output": "json",
                    "matchType": "domain",
                    "filter": [
                        status_filter,
                        "mime-detected:text/html",
                    ],
                    "collapse": "urlkey",
                    "limit": KNOWN_CAPTURES_PER_DOMAIN,
                },
            )
            if is_empty_cdx_result(response):
                return response
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 4:
                break
            delay = 0.5 * (2**attempt)
            report(
                f"select index: {domain} request failed; "
                f"retrying in {delay:g}s"
            )
            time.sleep(delay)
    assert last_error is not None
    raise last_error


def extend_from_candidates(
    captures: list[Capture],
    *,
    tier: Tier,
    target: int,
    candidates: list[dict[str, Any]],
    excluded: set[tuple[str, int, int]],
) -> None:
    if len(captures) >= target:
        report(f"select {tier}: {target}/{target} (cached)")
        excluded.update(capture_identity(capture) for capture in captures[:target])
        return
    used = {
        capture_identity(capture)
        for capture in captures
    }
    for item in candidates:
        key = candidate_identity(item)
        if key in used or key in excluded:
            continue
        captures.append(capture_from_cdx(tier, len(captures), item))
        used.add(key)
        if len(captures) == target:
            excluded.update(used)
            report(f"select {tier}: {len(captures)}/{target} complete")
            return
    raise RuntimeError(
        f"domain queries yielded only {len(captures)} unique {tier} pages; "
        + shortage_advice(tier)
    )


def shortage_advice(tier: Tier) -> str:
    if tier == "known":
        return "add known domains or reduce --known"
    if tier == "noise":
        return "increase --noise-index-shards or reduce --noise"
    return "increase --noise-index-shards or reduce --fail"


def candidate_identity(item: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(item["filename"]),
        int(item["offset"]),
        int(item["length"]),
    )


def capture_identity(capture: Capture) -> tuple[str, int, int]:
    return (capture.filename, capture.offset, capture.length)


def is_empty_cdx_result(response: httpx.Response) -> bool:
    if response.status_code != 404:
        return False
    try:
        message = str(response.json().get("message", ""))
    except ValueError:
        return False
    return message.startswith("No Captures found for:")


def capture_from_cdx(tier: Tier, ordinal: int, item: dict[str, Any]) -> Capture:
    timestamp = datetime.strptime(
        str(item["timestamp"]), "%Y%m%d%H%M%S"
    ).replace(tzinfo=UTC)
    return Capture(
        tier=tier,
        ordinal=ordinal,
        url=str(item["url"]),
        status=int(item["status"]),
        observed_at=timestamp.isoformat(),
        filename=str(item["filename"]),
        offset=int(item["offset"]),
        length=int(item["length"]),
        declared_media_type=item.get("mime"),
        charset=item.get("encoding"),
    )


def stable_key(seed: int, *values: str) -> bytes:
    digest = hashlib.sha256()
    digest.update(str(seed).encode())
    for value in values:
        digest.update(b"\0")
        digest.update(value.encode())
    return digest.digest()


def delete_excess(dataset: str, targets: Targets) -> int:
    """Delete only excess evidence owned by this exact disposable corpus."""
    from repository.catalogue import catalogue_from_env
    from repository.objects.config import object_store_from_env

    predicate = corpus_predicate(dataset)
    excess = excess_predicate(targets)
    with catalogue_from_env(threads=1) as catalogue:
        doomed = catalogue.trusted_remote_rows(
            """
            SELECT documents.object_key
            FROM ingest.visits AS visits
            JOIN ingest.documents AS documents USING (visit_id)
            WHERE """
            + predicate
            + " AND ("
            + excess
            + ")"
        )
        if not doomed:
            return 0
        with catalogue.remote_transaction():
            catalogue.trusted_remote_execute(
                """
                DELETE FROM ingest.documents
                WHERE visit_id IN (
                    SELECT visit_id FROM ingest.visits
                    WHERE """
                + predicate
                + " AND ("
                + excess
                + "))"
            )
            catalogue.trusted_remote_execute(
                "DELETE FROM ingest.crawls WHERE crawl_id IN "
                "(SELECT crawl_id FROM ingest.visits WHERE "
                + predicate
                + " AND ("
                + excess
                + "))"
            )
            catalogue.trusted_remote_execute(
                "DELETE FROM ingest.visits WHERE "
                + predicate
                + " AND ("
                + excess
                + ")"
            )
        candidates = tuple(dict.fromkeys(str(row[0]) for row in doomed))
        unreferenced: list[str] = []
        for batch in chunked(candidates, 500):
            values = ", ".join(sql_string(value) for value in batch)
            retained = {
                str(row[0])
                for row in catalogue.trusted_remote_rows(
                    "SELECT DISTINCT object_key FROM ingest.documents "
                    f"WHERE object_key IN ({values})"
                )
            }
            unreferenced.extend(value for value in batch if value not in retained)
    object_store_from_env().delete_many(tuple(unreferenced))
    return len(doomed)


def corpus_predicate(dataset: str) -> str:
    return (
        "provenance.kind::VARCHAR = 'external' "
        "AND provenance.system::VARCHAR = 'common-crawl' "
        f"AND provenance.dataset::VARCHAR = {sql_string(dataset)}"
    )


def excess_predicate(targets: Targets) -> str:
    clauses = []
    for tier in ("known", "noise", "failure"):
        target = targets.for_tier(tier)
        clauses.append(
            f"(split_part(provenance.source_record_id::VARCHAR, ':', 1) = "
            f"{sql_string(tier)} AND "
            "try_cast(split_part(provenance.source_record_id::VARCHAR, ':', 2) "
            f"AS BIGINT) >= {target})"
        )
    return " OR ".join(clauses)


def chunked(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def ingest_capture(
    capture: Capture,
    *,
    api_url: str,
    dataset: str,
    client: httpx.Client,
) -> str:
    end = capture.offset + capture.length - 1
    headers = {"Range": f"bytes={capture.offset}-{end}"}
    last_error: BaseException | None = None
    for attempt in range(5):
        try:
            response = client.get(
                f"{COMMON_CRAWL_DATA}/{capture.filename}",
                headers=headers,
            )
            response.raise_for_status()
            content, media_type, charset = extract_html(response.content)
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
        f"failed to ingest {capture.tier}:{capture.ordinal} {capture.url}"
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


def reconcile(arguments: argparse.Namespace) -> int:
    targets = targets_for(
        arguments.known,
        arguments.noise,
        arguments.failure_rate,
    )
    dataset = dataset_name(arguments.crawl, arguments.seed)
    path = manifest_path(
        arguments.cache_dir.resolve(),
        arguments.crawl,
        arguments.seed,
    )
    existing = (
        {"known": set(), "noise": set(), "failure": set()}
        if arguments.dry_run
        else query_existing(arguments.api_url, dataset)
    )
    report(
        f"dataset={dataset} desired={targets.total} "
        f"(known={targets.known}, noise={targets.noise}, "
        f"failure={targets.failure})"
    )
    report(
        "existing="
        f"{sum(len(ordinals) for ordinals in existing.values())} "
        f"(known={len(existing['known'])}, noise={len(existing['noise'])}, "
        f"failure={len(existing['failure'])})"
    )
    captures = load_manifest(path)
    grouped = captures_by_tier(captures)
    known_domains = load_domains(arguments.known_domains)
    report("phase=selection")
    known_candidates = (
        query_domain_candidates(
            label="known pages",
            domains=known_domains,
            crawl=arguments.crawl,
            seed=arguments.seed,
            status_filter="status:200",
            minimum_candidates=targets.known,
            minimum_domains=min(3, len(known_domains)),
        )
        if len(grouped["known"]) < targets.known
        else []
    )
    excluded: set[tuple[str, int, int]] = set()
    extend_from_candidates(
        grouped["known"],
        tier="known",
        target=targets.known,
        candidates=known_candidates,
        excluded=excluded,
    )
    if (
        len(grouped["noise"]) < targets.noise
        or len(grouped["failure"]) < targets.failure
    ):
        noise_candidates, failure_candidates = query_noise_index_candidates(
            crawl=arguments.crawl,
            seed=arguments.seed,
            cache_dir=arguments.cache_dir.resolve(),
            known_domains=known_domains,
            noise_target=targets.noise,
            failure_target=targets.failure,
            shard_limit=arguments.noise_index_shards,
        )
    else:
        noise_candidates, failure_candidates = [], []
    extend_from_candidates(
        grouped["noise"],
        tier="noise",
        target=targets.noise,
        candidates=noise_candidates,
        excluded=excluded,
    )
    extend_from_candidates(
        grouped["failure"],
        tier="failure",
        target=targets.failure,
        candidates=failure_candidates,
        excluded=excluded,
    )
    all_captures = [
        capture
        for tier in ("known", "noise", "failure")
        for capture in grouped[tier]
    ]
    save_manifest(path, all_captures)
    report(f"selection complete: {len(all_captures)} captures")
    if arguments.dry_run:
        print(
            f"dry-run: manifest cached at {path}; "
            "no WARC records downloaded and no Atlas data changed"
        )
        return 0

    report("phase=reconciliation")
    removed = delete_excess(dataset, targets)
    if removed:
        report(f"removed={removed} excess corpus observations")
        existing = query_existing(arguments.api_url, dataset)

    missing = [
        capture
        for tier in ("known", "noise", "failure")
        for capture in grouped[tier][: targets.for_tier(tier)]
        if capture.ordinal not in existing[tier]
    ]
    if missing:
        report(
            f"phase=submission missing={len(missing)} "
            f"concurrency={arguments.jobs}"
        )
        completed = 0
        dispositions: dict[str, int] = {}
        interval = progress_interval(len(missing))

        limits = httpx.Limits(
            max_connections=arguments.jobs,
            max_keepalive_connections=arguments.jobs,
        )
        with httpx.Client(
            timeout=120,
            follow_redirects=True,
            limits=limits,
        ) as client:

            def ingest_one(capture: Capture) -> str:
                return ingest_capture(
                    capture,
                    api_url=arguments.api_url,
                    dataset=dataset,
                    client=client,
                )

            with ThreadPoolExecutor(max_workers=arguments.jobs) as executor:
                dispositions_iter = executor.map(
                    ingest_one,
                    missing,
                    buffersize=arguments.jobs * 2,
                )
                for disposition in dispositions_iter:
                    dispositions[disposition] = (
                        dispositions.get(disposition, 0) + 1
                    )
                    completed += 1
                    if completed % interval == 0 or completed == len(missing):
                        report(f"submitted={completed}/{len(missing)}")
        report(
            "submission="
            + ", ".join(
                f"{name}:{count}" for name, count in sorted(dispositions.items())
            )
        )
    else:
        report("already reconciled")
    report(
        "Atlas accepted all requested evidence; ingestion and materialization "
        "continue asynchronously."
    )
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
