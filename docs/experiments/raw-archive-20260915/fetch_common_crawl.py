"""Download at most one bounded public Common Crawl WARC record per URL."""

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        "https://index.commoncrawl.org/collinfo.json", timeout=15
    ) as r:
        collection = json.loads(r.read(200_000))[0]
    manifest = []
    for url in ["example.com/", "www.iana.org/domains/reserved"]:
        entry = {"requested_url": url, "collection": collection["id"]}
        try:
            query = urllib.parse.urlencode(
                [
                    ("url", url),
                    ("output", "json"),
                    ("filter", "status:200"),
                    ("filter", "mime:text/html"),
                    ("collapse", "urlkey"),
                ]
            )
            with urllib.request.urlopen(
                collection["cdx-api"] + "?" + query, timeout=20
            ) as r:
                data = r.read(128_001)
            if len(data) > 128_000:
                raise ValueError("Index response exceeds bound")
            row = json.loads(data.splitlines()[0])
            offset, length = int(row["offset"]), int(row["length"])
            if not 0 < length <= 4 * 1024 * 1024:
                raise ValueError("Record exceeds bound")
            source = "https://data.commoncrawl.org/" + row["filename"]
            request = urllib.request.Request(
                source, headers={"Range": f"bytes={offset}-{offset + length - 1}"}
            )
            with urllib.request.urlopen(request, timeout=20) as r:
                if r.status != 206:
                    raise ValueError("Expected ranged response")
                record = r.read(length + 1)
            if len(record) != length:
                raise ValueError("Unexpected range length")
            name = hashlib.sha256(record).hexdigest() + ".warc.gz"
            (args.output / name).write_bytes(record)
            entry.update(
                {
                    "source": source,
                    "offset": offset,
                    "length": length,
                    "file": name,
                    "sha256": hashlib.sha256(record).hexdigest(),
                    "capture_timestamp": row["timestamp"],
                }
            )
        except Exception as exc:
            entry["error"] = type(exc).__name__
        manifest.append(entry)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
