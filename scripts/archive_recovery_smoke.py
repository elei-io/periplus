"""Real-store recovery drill on an isolated Docker network and copied raw archive.

Run with the local object-store environment configured. This reads the source
archive, writes only the copied archive and fresh recovery containers, and leaves
those containers available for inspection. No original database is read.
"""

import argparse
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import UUID

from periplus.ingestion.archive import Archive, PREFIX
from periplus.ingestion.captures import Capture, Payload
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.objects.store import FileObjectStore
from periplus.ingestion.objects.html import RawHtmlRepository
from periplus.materialization.recipe import recipe_digest, preserve_software

ROOT = Path(__file__).resolve().parents[1]


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".artifacts/archive-recovery/drill"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Archive(object_store_from_env())
    heads = source.heads()
    if sum(heads) > 1000:
        raise ValueError("Drill source exceeds its 1000-event copy budget")
    archive = Archive(FileObjectStore(output / "archive"))
    captures = {}
    for item in source.store.list_objects(PREFIX):
        with source.store.open(item.key) as body:
            archive.store.put_if_absent(item.key, body)
    for event in source.events(heads):
        capture = source.read(event.capture_id, event.digest)
        if source.retired(capture.capture_id):
            continue
        captures[str(capture.capture_id)] = capture
        if capture.payload:
            with source.store.open(capture.payload.object_key) as body:
                archive.store.put_if_absent(capture.payload.object_key, body)
    # A broad enough corpus to observe live worker ownership before interruption.
    for index in range(256):
        identity = UUID(int=900000 + index)
        observed = datetime(2026, 9, 15, tzinfo=UTC)
        stored = RawHtmlRepository(archive.store).put(
            f'<html><body><h1>Recovery {index}</h1><a href="/next">next</a></body></html>',
            source_url=f"https://recovery.invalid/{index}",
            visit_id=identity,
            observed_at=observed,
            content_type="text/html",
        )
        capture = Capture(
            capture_id=identity,
            requested_url=f"https://recovery.invalid/{index}",
            captured_at=observed,
            timestamp_precision="microsecond",
            http_status=200,
            completeness="complete",
            payload=Payload(
                content_id=stored.sha256,
                byte_length=stored.size_bytes,
                object_key=stored.object_key,
                stored_bytes=stored.compressed_size_bytes,
                storage_encoding="zstd",
                representation="rendered_html",
                media_type="text/html",
                charset="utf-8",
            ),
        )
        if not archive.retired(identity):
            archive.commit(capture)
            captures[str(identity)] = capture
    retired = UUID(int=900000)
    archive.retire(retired)
    captures.pop(str(retired), None)
    key, manifest = archive.manifest(recipe_digest(), preserve_software(archive.store))
    environment = dict(
        os.environ,
        RECOVERY_ARCHIVE_DIRECTORY=str(output / "archive"),
        RECOVERY_MANIFEST=key,
    )
    compose = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "tests/integration/archive-recovery/compose.yaml"),
    ]

    def command(*parts, check=True):
        result = subprocess.run(
            [*compose, *parts], env=environment, capture_output=True, text=True
        )
        with (output / "docker.log").open("a") as log:
            log.write(result.stdout + result.stderr)
        if check:
            result.check_returncode()
        return result.stdout.strip()

    def inspect(code):
        return command("run", "--rm", "--no-deps", "inspect", code)

    def pg(sql):
        return command(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "recovery",
            "-d",
            "recovery",
            "-Atc",
            sql,
        )

    def ch(sql):
        return command(
            "exec",
            "-T",
            "clickhouse",
            "clickhouse-client",
            "--user",
            "recovery",
            "--password",
            "recovery",
            "--query",
            sql,
        )

    def report(**value):
        value["at"] = datetime.now(UTC).isoformat()
        print(json.dumps(value), flush=True)
        with (output / "events.jsonl").open("a") as log:
            log.write(json.dumps(value) + "\n")

    command("down", "--volumes", "--remove-orphans")
    command("up", "-d", "postgres", "clickhouse", "nats")
    command("run", "--rm", "setup")
    assert pg("SELECT count(*) FROM collections") == "0"
    assert ch("SELECT count() FROM material.captures") == "0"
    # Four events per batch makes ownership and restart progress observable.
    pg("UPDATE material_builds SET page_size=4")
    command("up", "-d", "--no-deps", "--scale", "worker=2", "worker")
    workers = command("ps", "-q", "worker").splitlines()
    deadline = time.monotonic() + 90
    owner = None
    while time.monotonic() < deadline:
        active = pg(
            "SELECT worker_id FROM material_batches WHERE status='running' LIMIT 1"
        )
        if active:
            owner = next((worker for worker in workers if worker[:12] in active), None)
            if owner:
                break
        time.sleep(0.1)
    if owner is None:
        raise AssertionError("Did not observe an active worker claim")
    killed_at = time.monotonic()
    subprocess.run(["docker", "kill", owner], check=True, capture_output=True)
    report(stage="worker_killed_during_claim", container=owner[:12])
    time.sleep(2)
    assert int(pg("SELECT count(*) FROM material_batches WHERE owner IS NOT NULL")) > 0
    command("up", "-d", "--no-deps", "--scale", "worker=2", "worker")
    deadline = time.monotonic() + 750
    while time.monotonic() < deadline:
        state = pg(
            "SELECT phase||':'||coalesce(blocker,'')||':'||(verified_at IS NOT NULL)::text FROM material_builds"
        )
        if state == "serving::true":
            break
        if ":true" not in state and "recipe" in state:
            raise AssertionError(state)
        time.sleep(2)
    else:
        raise AssertionError(
            "Recovery did not finish within the bounded ownership drain"
        )
    actual = int(ch("SELECT count() FROM public_v1.capture"))
    assert actual == len(captures), (actual, len(captures))
    assert (
        ch(f"SELECT count() FROM material.captures WHERE capture_id='{retired}'") == "0"
    )
    assert pg("SELECT count(*) FROM collection_results") == "0"
    assert pg("SELECT count(*) FROM collections") == "0"
    assert (
        ch(
            "SELECT count() FROM (SELECT capture_id,count() AS n FROM material.captures GROUP BY capture_id HAVING n!=1)"
        )
        == "0"
    )
    actual_facts = json.loads(
        ch(
            "SELECT toString(capture_id) AS id,lower(hex(evidence_digest)) AS digest FROM material.captures ORDER BY id FORMAT JSON"
        )
    )["data"]
    assert {row["id"]: row["digest"] for row in actual_facts} == {
        key: value.digest for key, value in captures.items()
    }
    report(
        stage="archive_only_recovery_verified",
        captures=actual,
        original_captures=len(captures) - 255,
        elapsed_after_kill_seconds=round(time.monotonic() - killed_at, 2),
        manifest=key,
        business_rows=0,
        duplicate_captures=0,
        tombstones_respected=True,
    )
    (output / "result.json").write_text(
        json.dumps({"captures": actual, "manifest": key, "status": "passed"}, indent=2)
    )
    # Actual worker lifecycle on the same isolated stores. Timed crash recovery
    # above uses the full lease. Cleanup clocks below move only after all writers
    # stop; this tests physical reclamation without pretending to test its clock.
    def control(code):
        return inspect('from uuid import UUID; from periplus.materialization.rebuilds.control import BuildControl; c=BuildControl(); '+code)
    def until(predicate, label, seconds=120):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(1)
        raise AssertionError(label)
    candidate=control(f"i=c.create(2,{key!r}); b=c.get(i); c.change(i,b.revision,paused=True); print(i)").splitlines()[-1]
    until(lambda:pg(f"SELECT phase FROM material_builds WHERE id='{candidate}'")=='building','candidate preparation')
    time.sleep(3)
    historical=pg(f"SELECT sum(cursor) FROM material_build_ranges WHERE build_id='{candidate}'")
    live_id=UUID(int=999999)
    live_body=RawHtmlRepository(archive.store).put('<html><body><h1>Live during backfill</h1></body></html>',
        source_url='https://recovery.invalid/live',visit_id=live_id,observed_at=datetime.now(UTC),content_type='text/html')
    live_capture=Capture(capture_id=live_id,requested_url='https://recovery.invalid/live',captured_at=datetime.now(UTC),
        timestamp_precision='microsecond',http_status=200,completeness='complete',payload=Payload(content_id=live_body.sha256,
        byte_length=live_body.size_bytes,object_key=live_body.object_key,stored_bytes=live_body.compressed_size_bytes,
        storage_encoding='zstd',representation='rendered_html',media_type='text/html',charset='utf-8'))
    archive.commit(live_capture)  # Deliberately publish no NATS notification.
    database='material_'+UUID(candidate).hex
    until(lambda:ch(f"SELECT count() FROM {database}.captures WHERE capture_id='{live_id}'")=='1','live work while history paused')
    assert pg(f"SELECT sum(cursor) FROM material_build_ranges WHERE build_id='{candidate}'")==historical
    report(stage='paused_history_live_capture_without_notification',build=candidate)
    broken=captures[str(UUID(int=900200))]
    body_path=output/'archive'/broken.payload.object_key
    original=body_path.read_bytes()
    body_path.unlink()
    control(f"c.action(UUID({candidate!r}),'resume')")
    until(lambda:int(pg(f"SELECT count(*) FROM material_batches WHERE build_id='{candidate}' AND status='failed'"))>0,'missing payload must fail')
    rejected=inspect(f"from uuid import UUID; from periplus.materialization.rebuilds.control import BuildControl,RebuildConflict\ntry: BuildControl().action(UUID({candidate!r}),'activate')\nexcept RebuildConflict: print('blocked')")
    assert rejected.splitlines()[-1]=='blocked'
    body_path.write_bytes(b'corrupt')
    try:archive.verify_payload(broken)
    except Exception:pass
    else:raise AssertionError('Corrupt payload accepted')
    body_path.write_bytes(original)
    control(f"c.action(UUID({candidate!r}),'retry')")
    # Read failure may retain an exact claim until its safe expiry. Exercise the
    # ordinary retry path with its real clock, without deleting claims manually.
    until(lambda:pg(f"SELECT phase FROM material_builds WHERE id='{candidate}'")=='ready','repaired batch retry',seconds=750)
    control(f"c.action(UUID({candidate!r}),'activate')")
    assert pg("SELECT build_id FROM material_publications WHERE api_version='public_v1'")==candidate
    assert int(ch(f'SELECT count() FROM query_{UUID(candidate).hex}.capture'))==actual+1
    report(stage='missing_and_corrupt_payload_blocked_then_repaired',build=candidate)
    cancelled=control(f"i=c.create(1,{key!r}); b=c.get(i); c.change(i,b.revision,paused=True); print(i)").splitlines()[-1]
    until(lambda:pg(f"SELECT phase FROM material_builds WHERE id='{cancelled}'")=='building','cancel candidate preparation')
    control(f"c.action(UUID({cancelled!r}),'cancel')")
    assert pg("SELECT build_id FROM material_publications WHERE api_version='public_v1'")==candidate
    command('stop','worker')
    pg(f"UPDATE material_builds SET drain_after=now() WHERE id='{cancelled}'")
    command('up','-d','--no-deps','--scale','worker=2','worker')
    until(lambda:pg(f"SELECT phase FROM material_builds WHERE id='{cancelled}'")=='cancelled','cancelled target reclamation')
    assert ch(f"SELECT count() FROM system.databases WHERE name='material_{UUID(cancelled).hex}'")=='0'
    replacement=control(f"print(c.create(128,{key!r}))").splitlines()[-1]
    until(lambda:pg(f"SELECT phase FROM material_builds WHERE id='{replacement}'")=='ready','replacement build')
    control(f"c.action(UUID({replacement!r}),'activate')")
    assert int(ch(f'SELECT count() FROM query_{UUID(replacement).hex}.capture'))==actual+1
    command('stop','worker')
    pg("UPDATE material_builds SET drain_after=now() WHERE phase='draining'")
    command('up','-d','--no-deps','--scale','worker=2','worker')
    until(lambda:pg("SELECT phase FROM material_builds WHERE id='00000000-0000-0000-0000-000000000001'")=='retired','previous target reclamation')
    assert ch("SELECT count() FROM system.databases WHERE name='material'")=='0'
    assert pg('SELECT count(*) FROM collections')=='0'
    report(stage='cancellation_publication_and_physical_reclamation_verified',cleanup_clock='accelerated_only_after_all_workers_stopped',serving=replacement)
    (output/'lifecycle-result.json').write_text(json.dumps({'status':'passed','serving':replacement,'captures':actual+1},indent=2))


if __name__ == "__main__":
    run()
