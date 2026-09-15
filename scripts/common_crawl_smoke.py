"""Admin import -> local S3/NATS/ClickHouse -> public SQL and exact bytes.

Uses real Common Crawl and localhost Compose only. No database resets.
"""
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4
import httpx
from periplus.platform.config import get_str


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    report = {}
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=20, trust_env=False,
                      headers={"Authorization": "Bearer " + get_str("PERIPLUS_ADMIN_API_TOKEN")}) as api:
        def submit(urls, *, budget=64 * 1024 * 1024):
            identity = str(uuid4())
            payload = {"id": identity, "specification": {"dataset": "CC-MAIN-2026-34", "urls": urls,
                "captured_from": "2026-08-01T00:00:00Z", "captured_until": "2026-09-01T00:00:00Z",
                "max_download_bytes": budget}}
            response = api.post("/operations/archive-imports", json=payload)
            response.raise_for_status()
            # Same submission identity must not start another job.
            repeated = api.post("/operations/archive-imports", json=payload)
            repeated.raise_for_status()
            assert repeated.json()["id"] == identity
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                response = api.get("/operations/archive-imports/" + identity)
                response.raise_for_status()
                job = response.json()
                if job["status"] in ("completed", "blocked", "cancelled"):
                    return job
                time.sleep(1)
            raise AssertionError(f"import did not settle: {identity}")

        obsolete = api.post("/collections", json={"specification": {
            "crawler": "common_crawl", "seed_urls": ["https://example.com/"], "page_limit": 1}})
        assert obsolete.status_code == 422, obsolete.text
        public = api.get("/operations/archive-imports", headers={
            "Authorization": "Bearer " + get_str("PERIPLUS_PUBLIC_API_TOKEN")})
        assert public.status_code == 403, public.text
        first = submit(["https://example.org/", "https://example.com/periplus-import-proof-" + uuid4().hex])
        assert first["status"] == "completed", first
        capture, missing = first["progress"]["results"]
        assert capture["status"] == "published", first
        assert missing["status"] == "missing", first
        repeated = submit(["https://example.org/"])
        assert repeated["status"] == "completed", repeated
        again = repeated["progress"]["results"][0]
        assert again["capture_id"] == capture["capture_id"] and again["already_archived"], repeated
        limited = submit(["https://example.com/"], budget=1)
        assert limited["status"] == "blocked" and limited["progress"]["reserved_download_bytes"] == 0, limited
        cancelled = api.post(f"/operations/archive-imports/{limited['id']}/cancel")
        cancelled.raise_for_status()
        assert cancelled.json()["status"] == "cancelled"
        report.update(import_job=first, repeated_job=repeated, budget_job=cancelled.json(),
                      collection_crawler_field_rejected=True, public_import_access_denied=True)

    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=30, trust_env=False,
                      headers={"Authorization": "Bearer " + get_str("PERIPLUS_QUERY_API_TOKEN")}) as query:
        deadline = time.monotonic() + 180
        rows = []
        while time.monotonic() < deadline:
            response = query.post("/query/exec", json={"sql":
                "SELECT c.capture_id,c.captured_at,c.content_id,e.text FROM public_v1.capture c "
                "JOIN public_v1.html_element e ON c.content_id=e.content_id "
                "WHERE c.capture_id=? AND e.tag='h1'", "parameters": [capture["capture_id"]]})
            response.raise_for_status()
            rows = response.json()["rows"]
            if rows:
                break
            time.sleep(1)
        assert len(rows) == 1 and rows[0][3] == "Example Domain", rows
        raw = httpx.get("http://127.0.0.1:8080/api/content/" + rows[0][2], timeout=30, trust_env=False)
        raw.raise_for_status()
        assert sha256(raw.content).hexdigest() == rows[0][2]
        report.update(public_rows=rows, raw_bytes=len(raw.content), raw_sha256=rows[0][2])
    destination = ROOT / ".artifacts/raw-archive-20260915/admin-import-e2e.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2))
    print(json.dumps({"import_id": first["id"], "capture_id": capture["capture_id"],
                      "status": "passed", "evidence": str(destination)}))


if __name__ == "__main__":
    main()
