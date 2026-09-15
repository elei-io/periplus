"""Run preserved business SQL sequentially through the public HTTP query API.

No engine connections, grants, settings overrides or hidden SQL optimizations.
The first execution is not certified cold. Results are digested, not retained.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import time
import tomllib
from typing import Any

ROOT = Path(__file__).resolve().parent


def bind_scope(sql: str, scope: int) -> tuple[str, list[int]]:
    return sql.replace("$scope", "?"), [scope] * sql.count("$scope")


def execute(endpoint: str, sql: str, parameters: list[int], phase: str) -> dict[str, Any]:
    started = time.perf_counter()
    process = subprocess.run(
        ["curl", "--silent", "--show-error", "--max-time", "55", "--request", "POST",
         "--header", "Content-Type: application/json", "--data-binary", "@-",
         "--write-out", "\n%{http_code}", endpoint.rstrip("/") + "/" + phase],
        input=json.dumps({"sql": sql, "parameters": parameters}), capture_output=True, text=True,
        timeout=60,
    )
    result: dict[str, Any] = {"wall_ms": (time.perf_counter()-started)*1000}
    if process.returncode:
        return result | {"status": "transport_failure", "curl_exit": process.returncode}
    body, status = process.stdout.rsplit("\n", 1)
    result["http_status"] = int(status)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return result | {"status": "gateway_failure", "error": "Non-JSON response"}
    if int(status) != 200:
        return result | {"status": "rejected", "error": payload}
    if phase == "prep":
        return result | {"status": "prepared", **payload}
    rows = payload.pop("rows")
    canonical_rows = sorted(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) for row in rows)
    return result | payload | {
        "status": "truncated" if payload["truncated"] else "completed",
        "answer_digest": sha256(json.dumps(canonical_rows, ensure_ascii=False).encode()).hexdigest(),
        "ordered_answer_digest": sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "returned_rows": len(rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", required=True, help="Case id, or explicit all")
    parser.add_argument("--endpoint", required=True, help="Public API query prefix, e.g. https://periplus.dev/api/query")
    parser.add_argument("--phase", choices=("prep", "exec"), default="exec")
    parser.add_argument("--warm-runs", type=int, choices=(0,1,2), default=1)
    parser.add_argument("--scope", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = sorted(ROOT.joinpath("cases").glob("*/case.toml"))
    selected = [p for p in cases if "all" in args.case or p.parent.name in args.case]
    if not selected or ("all" not in args.case and set(args.case) != {p.parent.name for p in selected}):
        parser.error("Unknown benchmark case")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:
        for path in selected:
            metadata = tomllib.loads(path.read_text())
            original = path.with_name("query.sql").read_text()
            parameters: list[int] = []
            sql = original
            if "$scope" in sql:
                scope = args.scope or metadata["scales"][0]
                sql, parameters = bind_scope(sql, scope)
            for attempt in range(1 + (args.warm_runs if args.phase == "exec" else 0)):
                started_at = datetime.now(UTC).isoformat()
                result = execute(args.endpoint, sql, parameters, args.phase)
                row = {"case": metadata["id"], "metadata": metadata,
                       "started_at": started_at, "attempt": attempt,
                       "original_sha256": sha256(original.encode()).hexdigest(),
                       "phase": args.phase, "parameters": parameters, **result}
                output.write(json.dumps(row, ensure_ascii=False) + "\n"); output.flush()
                print(json.dumps({k:row.get(k) for k in ("case","attempt","status","wall_ms","returned_rows","query_id")}), flush=True)
                if row["status"] in {"transport_failure", "gateway_failure"} or row.get("http_status") in {401, 403, 429, 503}:
                    raise SystemExit("Resolve availability/admission before continuing the suite")
                if row["status"] != "completed":
                    break  # Do not repeatedly run a rejected or incomplete query.


if __name__ == "__main__":
    main()
