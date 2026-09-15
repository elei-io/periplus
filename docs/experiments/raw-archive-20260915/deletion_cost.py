"""Measure selective reclamation on the same real HTML sample, locally only.

The protected-content test is a policy model, not a distributed concurrency test.
The packed candidate uses 32 distinct contents per file, not one giant archive.
"""

import argparse
import json
import tempfile
import time
from pathlib import Path

from proof import (
    Store,
    digest,
    encode,
    fixture_bodies,
    fixtures,
    install_cas,
    read_warc,
    recover_cas,
    recover_packed,
    warc,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bodies = fixture_bodies(args.sample)
    rows = fixtures(bodies, 2)
    hashes = list(bodies)
    # One selected hash in every 32-content pack, deliberately dispersed rather
    # than chosen by size. Same selected set for both storage candidates.
    selected = set(hashes[::32])
    protected = {hashes[0]}
    eligible = selected - protected
    deleted = {r["capture_id"] for r in rows if r["content_sha256"] in eligible}
    results = {
        "contents": len(bodies),
        "captures": len(rows),
        "pack_contents": 32,
        "selected_contents": len(selected),
        "protected_contents": len(protected),
        "eligible_contents": len(eligible),
        "deleted_captures": len(deleted),
        "selection": "One hash at each group-of-32 boundary; first selected content protected.",
    }
    with tempfile.TemporaryDirectory(prefix="periplus-raw-delete-") as tmp:
        cas = Store(Path(tmp) / "cas")
        install_cas(cas, bodies, rows)
        before = cas.footprint()
        cas.calls.clear()
        cas.read_bytes = cas.write_bytes = 0
        start = time.perf_counter()
        for cid in sorted(deleted):
            cas.put("deleted/" + cid + ".json", encode({"capture_id": cid}))
        for sha in eligible:
            cas.delete("content/" + sha + ".zst")
        stats = {
            "seconds": time.perf_counter() - start,
            "requests": dict(cas.calls),
            "read_bytes": cas.read_bytes,
            "write_bytes": cas.write_bytes,
            "net_reclaimed_bytes": before - cas.footprint(),
        }
        captures, remaining = recover_cas(Store(cas.root))
        assert set(remaining) == set(bodies) - eligible
        assert all(cid not in captures for cid in deleted)
        assert protected <= remaining.keys()
        results["cas"] = stats
        packed = Store(Path(tmp) / "packed")
        for offset in range(0, len(hashes), 32):
            group = set(hashes[offset : offset + 32])
            data, _ = warc(
                {h: bodies[h] for h in hashes if h in group},
                [r for r in rows if r["content_sha256"] in group],
            )
            packed.put("hot/" + str(offset) + ".warc.gz", data)
        before = packed.footprint()
        packed.calls.clear()
        packed.read_bytes = packed.write_bytes = 0
        start = time.perf_counter()
        rewritten = 0
        for cid in sorted(deleted):
            packed.put("deleted/" + cid + ".json", encode({"capture_id": cid}))
        for offset in range(0, len(hashes), 32):
            group = set(hashes[offset : offset + 32])
            if not group & eligible:
                continue
            old = "hot/" + str(offset) + ".warc.gz"
            resources, captures = read_warc(packed.get(old))
            data, _ = warc(
                {h: b for h, b in resources.items() if h not in eligible},
                [r for r in captures if r["capture_id"] not in deleted],
            )
            key = "packs/" + digest(data) + ".warc.gz"
            packed.put(key, data)
            got, kept = read_warc(packed.get(key))
            assert not eligible & got.keys() and not deleted & {
                c["capture_id"] for c in kept
            }
            receipt = {
                "inputs": [old],
                "outputs": [key],
                "sha256": digest(data),
                "version": 1,
            }
            packed.put(
                "replacements/" + digest(encode(receipt)) + ".json", encode(receipt)
            )
            packed.delete(old)
            rewritten += 1
        stats = {
            "seconds": time.perf_counter() - start,
            "requests": dict(packed.calls),
            "read_bytes": packed.read_bytes,
            "write_bytes": packed.write_bytes,
            "net_reclaimed_bytes": before - packed.footprint(),
            "archives_rewritten": rewritten,
        }
        captures, remaining = recover_packed(Store(packed.root))
        assert set(remaining) == set(bodies) - eligible
        assert all(cid not in captures for cid in deleted)
        assert protected <= remaining.keys()
        results["packed"] = stats
        results["policy_tests"] = {
            "active_collection_content_preserved": True,
            "all_unprotected_selected_content_physically_removed": True,
            "object_only_recovery_excludes_deleted_captures": True,
            "distributed_recheck_and_fencing_tested": False,
        }
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
