"""Local Compose: die after raw commit, replay, then rebuild imported evidence.

Uses a fixed real CC record. Run the crash-gap case before that record has been
ingested; later runs verify idempotent replay instead and report that distinction.
"""

import json
from pathlib import Path
import subprocess
import time

import httpx
from periplus.platform.config import get_str


def container(code: str, *, expected: int = 0) -> str:
    result = subprocess.run(["docker", "compose", "exec", "-T", "periplus-crawler", "python", "-c", code],
                            capture_output=True, text=True)
    if result.returncode != expected:
        raise RuntimeError(result.stderr or result.stdout)
    return result.stdout


def main():
    code = '''
import json, os
from periplus.ingestion.common_crawl import CommonCrawlRecord, CommonCrawlClient
from periplus.ingestion.archive_import import archive_record
from periplus.ingestion.archive import archive_key
from periplus.ingestion.objects.config import object_store_from_env
from periplus.ingestion.queue import visit_ingestion_job
item = CommonCrawlRecord(dataset="CC-MAIN-2026-34", url="https://www.iana.org/domains/reserved?", timestamp="20260809105111",
 filename="crawl-data/CC-MAIN-2026-34/segments/1786091385223.27/warc/CC-MAIN-20260809093337-20260809123337-00416.warc.gz",
 offset=650133721, length=3972)
client = CommonCrawlClient()
evidence = archive_record(object_store_from_env(), item, client.fetch(item))
client.close()
print(json.dumps({'capture_id':str(evidence.visit.visit_id), 'archive_key':archive_key(visit_ingestion_job(evidence))}), flush=True)
os._exit(31)
'''
    result = json.loads(container(code, expected=31))
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30, trust_env=False,
                     headers={"Authorization": "Bearer " + get_str("PERIPLUS_ADMIN_API_TOKEN")}) as api, \
         httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False,
                     headers={"Authorization": "Bearer " + get_str("PERIPLUS_QUERY_API_TOKEN")}) as query:
        def rows():
            response = query.post("/query/exec", json={"sql":
                "SELECT c.capture_id,c.captured_at,e.text FROM public_v1.capture c "
                "JOIN public_v1.html_element e USING(content_id) WHERE c.capture_id=? AND e.tag='h1'",
                "parameters": [result["capture_id"]]})
            response.raise_for_status()
            return response.json()["rows"]
        result["hidden_after_process_death"] = rows() == []
        replay = subprocess.run(["docker", "compose", "exec", "-T", "periplus-crawler", "periplus-archive", "replay",
            "--prefix", "raw/v1/evidence/common-crawl/CC-MAIN-2026-34/", "--limit", "1000"], capture_output=True, text=True)
        if replay.returncode:
            raise RuntimeError(replay.stderr)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            recovered = rows()
            if recovered:
                assert len(recovered) == 1 and recovered[0][2] == "IANA-managed Reserved Domains", recovered
                break
            time.sleep(1)
        else:
            raise TimeoutError("raw-journal replay did not materialize the archived capture")
        result["replayed_rows"] = recovered
        print(json.dumps(result), flush=True)
        created = api.post("/operations/materializations/runs", json={"page_size": 2})
        created.raise_for_status()
        identity = created.json()["id"]
        path = "/operations/materializations/runs/" + identity
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = api.get(path)
            response.raise_for_status()
            build = response.json()
            if build["blocker"]:
                raise RuntimeError(str(build["blocker"]))
            if (build["phase"] == "ready" and build["live_pending"] == 0
                    and build["ingestion_floor"] >= build["barrier"]
                    and build["material_floor"] >= build["barrier"]):
                break
            time.sleep(1)
        else:
            raise TimeoutError("background rebuild did not become ready")
        response = api.post(path + "/actions", json={"action": "activate"})
        response.raise_for_status()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = api.get(path)
            response.raise_for_status()
            if response.json()["phase"] == "serving":
                break
            time.sleep(1)
        else:
            raise TimeoutError("rebuilt target did not activate")
        assert rows() == recovered
        result.update(rebuild_id=identity, survived_rebuild_activation=True)
    path = Path(".artifacts/raw-archive-20260915/recovery-e2e.json")
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
