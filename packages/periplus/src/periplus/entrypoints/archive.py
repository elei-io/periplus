"""Archive import, immutable recovery manifests, verification and worker rebuilds."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from periplus.ingestion.archive import Archive
from periplus.ingestion.objects.config import object_store_from_env
from periplus.materialization.recipe import (
    preserve_software,
    recipe_digest,
    verify_software,
)


async def run(args):
    store = object_store_from_env()
    archive = Archive(store)
    if args.command == "manifest":
        key, manifest = archive.manifest(recipe_digest(), preserve_software(store))
        print(json.dumps({"key": key, **manifest.model_dump(mode="json")}))
        return
    if args.command in ("verify", "restore"):
        manifest = archive.read_manifest(args.manifest)
        verify_software(store, manifest.software_key)
        if args.command == "restore":
            from periplus.materialization.rebuilds.control import BuildControl

            print(BuildControl().create(args.page_size, args.manifest))
            return
        retained = retired = 0
        for event in archive.events(manifest.heads):
            capture = archive.read(event.capture_id, event.digest)
            if archive.retired(capture.capture_id):
                retired += 1
            else:
                archive.verify_payload(capture)
                retained += 1
        print(
            json.dumps(
                {"verified_retained_events": retained, "retired_events": retired}
            )
        )
        return
    from periplus.ingestion.archive_import import import_record
    from periplus.ingestion.common_crawl import CommonCrawlClient, CommonCrawlRecord
    from periplus.ingestion.queue import ArchivePublisher

    queue = ArchivePublisher(archive=archive)
    client = CommonCrawlClient()
    await queue.connect()
    try:
        if args.command == "lookup":
            now = datetime.now(UTC)
            for url in args.url:
                item = await asyncio.to_thread(
                    client.lookup,
                    url,
                    args.dataset,
                    since=now - timedelta(days=args.max_age_days),
                    until=now,
                )
                if item is None:
                    print(json.dumps({"url": url, "status": "missing"}))
                    continue
                capture = await import_record(client, store, queue, item)
                print(
                    json.dumps(
                        {"capture_id": str(capture.capture_id), "status": "archived"}
                    )
                )
        else:
            items = []
            with args.manifest.open() as stream:
                for line in stream:
                    if len(line) > 32768 or len(items) >= args.limit:
                        raise ValueError("Import manifest exceeds bounds")
                    if line.strip():
                        items.append(CommonCrawlRecord.model_validate_json(line))
            for item in sorted(items, key=lambda x: (x.filename, x.offset)):
                capture = await import_record(client, store, queue, item)
                print(
                    json.dumps(
                        {"capture_id": str(capture.capture_id), "status": "archived"}
                    )
                )
    finally:
        client.close()
        await queue.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "manifest", help="Preserve the software recipe and an immutable archive cut"
    )
    verify = sub.add_parser(
        "verify", help="Verify a raw recovery manifest without any database or queue"
    )
    verify.add_argument("--manifest", required=True)
    restore = sub.add_parser(
        "restore", help="Request a worker rebuild from a raw manifest after fresh setup"
    )
    restore.add_argument("--manifest", required=True)
    restore.add_argument("--page-size", type=int, default=32, choices=range(1, 129))
    lookup = sub.add_parser("lookup")
    lookup.add_argument("--dataset", required=True)
    lookup.add_argument("--url", action="append", required=True)
    lookup.add_argument("--max-age-days", type=int, required=True)
    imports = sub.add_parser("import")
    imports.add_argument("--manifest", type=Path, required=True)
    imports.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.command == "lookup" and (
        not 1 <= args.max_age_days <= 3650 or len(args.url) > 100
    ):
        parser.error("Lookup accepts 1–100 URLs and 1–3650 days")
    if args.command == "import" and not 1 <= args.limit <= 100000:
        parser.error("Import limit must be 1–100000")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
