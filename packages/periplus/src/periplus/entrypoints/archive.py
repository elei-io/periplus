"""Bounded operator archive import and raw-evidence replay."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from periplus.ingestion.archive import PREFIX, archived_jobs
from periplus.ingestion.archive_import import import_record
from periplus.ingestion.common_crawl import CommonCrawlClient, CommonCrawlRecord
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.queue import IngestionQueueClient


async def run(args: argparse.Namespace) -> None:
    store = object_store_from_env()
    queue = IngestionQueueClient()
    client = CommonCrawlClient()
    await queue.connect()
    try:
        if args.command == "replay":
            count = 0
            for job in archived_jobs(store, args.prefix):
                if count >= args.limit:
                    raise ValueError("replay limit reached; narrow the prefix or raise --limit")
                await queue.reconcile(job)
                count += 1
                print(json.dumps({"capture_id": str(job.identity), "status": "replayed"}), flush=True)
            return
        if args.command == "lookup":
            now = datetime.now(UTC)
            for url in args.url:
                item = await asyncio.to_thread(client.lookup, url, args.dataset,
                    since=now - timedelta(days=args.max_age_days), until=now)
                if item is None:
                    print(json.dumps({"url": url, "status": "missing", "next": "live_crawl"}), flush=True)
                    continue
                evidence = await import_record(client, store, queue, item)
                print(json.dumps({"url": url, "status": "archived", "capture_id": str(evidence.visit.visit_id),
                                  "captured_at": evidence.visit.observed_at.isoformat()}), flush=True)
        else:
            # A bounded manifest is sorted by archive and offset. Large corpus
            # planners produce multiple manifests; the importer holds no corpus.
            with args.manifest.open() as stream:
                items = []
                for line in stream:
                    if len(line) > 32768 or len(items) >= args.limit:
                        raise ValueError("manifest exceeds configured bounds")
                    if line.strip():
                        items.append(CommonCrawlRecord.model_validate_json(line))
            for item in sorted(items, key=lambda x: (x.filename, x.offset)):
                evidence = await import_record(client, store, queue, item)
                print(json.dumps({"capture_id": str(evidence.visit.visit_id), "status": "archived"}), flush=True)
    finally:
        client.close()
        await queue.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    lookup = sub.add_parser("lookup", help="import suitable CC captures for exact URLs")
    lookup.add_argument("--dataset", required=True)
    lookup.add_argument("--url", action="append", required=True)
    lookup.add_argument("--max-age-days", type=int, required=True)
    manifest = sub.add_parser("import", help="import a bounded JSONL CC index selection")
    manifest.add_argument("--manifest", type=Path, required=True)
    manifest.add_argument("--limit", type=int, default=1000)
    replay = sub.add_parser("replay", help="republish frozen evidence using only raw archive inputs")
    replay.add_argument("--prefix", default=PREFIX)
    replay.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if getattr(args, "limit", 1) not in range(1, 100001):
        parser.error("limit must be between 1 and 100000")
    if args.command == "lookup" and (not 1 <= args.max_age_days <= 3650 or len(args.url) > 100):
        parser.error("lookup accepts 1–100 URLs and 1–3650 days")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
