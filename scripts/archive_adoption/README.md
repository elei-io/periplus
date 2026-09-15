# One-time homelab archive adoption

**The user selected separate Zstd bodies and batched metadata on 2026-09-15.**
Full verification subsequently passed for all 176,750 retained bodies. See
[the adoption handoff](../../docs/ARCHIVE_ADOPTION.md) for publication status,
manifest identity and recovery evidence. Keep old writers stopped throughout.

This is an operator conversion for the retained old lake, outside the runtime.
It reuses the current `Capture` and `Archive` contracts. It does not install a
compatibility reader, copy HTML, retire captures, or delete any objects.

1. Save deployment/controller settings and close admission; pause frontier
   dispatch and let started captures finish. Suspend Periplus reconciliation and
   KEDA, then gracefully stop all old Periplus processes and LakeDucktor. Keep
   unrelated homelab workloads running.
2. Take custom-format dumps of `periplus_control` and `periplus_lake`. Preserve
   the old lake files as well: its metadata dump alone is not a complete lake
   backup. Export `retired_evidence` as a JSON array including kind, identity and
   retired_at. Keep these private, ignored operator artifacts.
3. Execute `export_legacy.py` using the deployed old image and read-only lake
   credentials. Capture stdout as `final-inventory.jsonl.gz`. It pins a DuckLake
   transaction, records its snapshot, preserves visits without documents, and
   rejects missing document references or duplicate visit identities.
4. Run `adopt.py --inventory <file> --retired <file> --output <report>` using the
   current package environment and the retained S3 repository configuration.
   This validates all capture mappings and hashes every unique retained payload.
   It does not write anything to the repository.
5. Repeat with `--commit`. It verifies the payloads, commits batches of up to 64 captures through the normal
   archive writer with one writer per shard, and reads every journal event and
   embedded capture back. Only an exact source/retained fingerprint match permits
   creating the recovery manifest and preserved software bundle. Existing archive
   events must match the source; unrelated data/retirements fail closed. Exact
   retries reuse immutable receipts; no external resume database is necessary.
   Verification uses one bounded GET per unique object and checks the actual
   compressed length, decoded length and SHA-256. An in-memory set binds those
   checks to the exact body metadata; commits accept only that verified set.
   This requires old writers to remain stopped and retained payloads to remain
   immutable for the run. A completed read-only verification can be reused with
   `--commit --verified-report <report>` only while the same immutable repository
   and stopped writers remain in place. The report must exactly match the input
   fingerprint, counts, inventory hash and repository identity. The final audit
   includes its SHA-256. Otherwise restarting verifies all bodies again.
6. Keep old writers stopped. Hand off the manifest key and report for a fresh
   control-database/ClickHouse recovery. Do not delete the old lake or the retained
   HTML until that recovery is accepted. Preserve the matching container/runtime
   dependencies in addition to the source/lock software bundle for offline use.

The source's retired observation IDs are excluded, even if an interrupted old
retirement left a visit visible. Already-deleted observations are not fabricated
as new capture envelopes. The saved retirement inventory is conversion evidence;
the new manifest includes only retained captures. Old queued crawl requests are
operational state in the control dump, not invented capture observations.

Capture identity, URLs, observation timestamp (including unknown timestamps),
HTTP status and body interpretation are preserved. Operational outcome/retry/
collection fields stay in the frozen source export and database backup. The old
deployment predates Common Crawl provenance; this tool must not be used on a
different source schema without reviewing its field mapping.

Run targeted tests from `packages/periplus`:

```sh
uv run python -m unittest discover -s ../../scripts/archive_adoption -p 'test_*.py'
```

`make check` also passed during preparation. Raw inventories, dumps, URLs and
execution logs belong under ignored `.artifacts/archive-adoption/`, never Git.
