"""Reconcile a deterministic Common Crawl corpus into a disposable Atlas lake."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
DEFAULT_DOMAINS = ROOT / "backend" / "tests" / "corpus" / "known_domains.txt"
DEFAULT_CACHE = ROOT / ".atlas" / "test-corpus"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_CRAWL = "CC-MAIN-2026-25"
DEFAULT_SEED = 20260727
DATASET_VERSION = "v1"
COMMON_CRAWL_DATA = "https://data.commoncrawl.org"
COMMON_CRAWL_INDEX = "https://index.commoncrawl.org"
KNOWN_CAPTURES_PER_DOMAIN = 1_000

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
        default=DEFAULT_DOMAINS,
        help="newline-delimited known-domain list",
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
        help="show the reconciliation without changing Atlas or downloading WARC data",
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
        raise ValueError(f"known-domain list is empty: {path}")
    if any("/" in domain or ":" in domain for domain in domains):
        raise ValueError("known-domain entries must be bare domain names")
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
            response = get_cdx_response(
                client,
                crawl=crawl,
                domain=domain,
                status_filter=status_filter,
            )
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
        "add domains or reduce --known"
    )


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
    if arguments.dry_run:
        print("dry-run: no Common Crawl data downloaded and no Atlas data changed")
        return 0

    captures = load_manifest(path)
    grouped = captures_by_tier(captures)
    domains = load_domains(arguments.known_domains)
    report("phase=selection")
    successful_candidates = (
        query_domain_candidates(
            label="successful pages",
            domains=domains,
            crawl=arguments.crawl,
            seed=arguments.seed,
            status_filter="status:200",
            minimum_candidates=targets.known + targets.noise,
            minimum_domains=min(3, len(domains)),
        )
        if (
            len(grouped["known"]) < targets.known
            or len(grouped["noise"]) < targets.noise
        )
        else []
    )
    excluded: set[tuple[str, int, int]] = set()
    extend_from_candidates(
        grouped["known"],
        tier="known",
        target=targets.known,
        candidates=successful_candidates,
        excluded=excluded,
    )
    extend_from_candidates(
        grouped["noise"],
        tier="noise",
        target=targets.noise,
        candidates=successful_candidates,
        excluded=excluded,
    )
    failure_candidates = (
        query_domain_candidates(
            label="failed pages",
            domains=domains,
            crawl=arguments.crawl,
            seed=arguments.seed,
            status_filter="!status:200",
            minimum_candidates=targets.failure,
            minimum_domains=1,
        )
        if len(grouped["failure"]) < targets.failure
        else []
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
