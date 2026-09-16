"""Bounded native ClickHouse benchmark transport through the homelab operator.

No runtime query rewrites, grants, publication changes or credentials in files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

HOMELAB = Path(os.environ.get("PERIPLUS_BENCH_HOMELAB", "/Users/ekku/Code/homelab"))
DATABASE = os.environ.get("PERIPLUS_BENCH_DATABASE", "bench_access_20260916")
if not re.fullmatch(r"bench_access_[a-z0-9_]+", DATABASE):
    raise ValueError("An isolated bench_access_ database is required")
ARTIFACTS = Path(__file__).resolve().parents[3] / ".artifacts" / "access-paths"
BYPASS_CACHES = (
    "enable_filesystem_cache=0,use_text_index_tokens_cache=0,"
    "use_text_index_negative_tokens_cache=0,use_text_index_header_cache=0,"
    "use_text_index_postings_cache=0"
)


def execute(
    sql: str,
    *,
    label: str = "diagnostic",
    read: bool = True,
    seconds: int = 45,
    memory: int | None = None,
    row_limit: int = 1001,
    workload: str | None = None,
) -> dict[str, Any]:
    identity = "access-" + uuid4().hex
    command = [
        "uv",
        "run",
        "--locked",
        "python",
        "scripts/operator.py",
        "kubectl",
        "exec",
        "-i",
        "-n",
        "state",
        "clickhouse-0",
        "--",
        "clickhouse-client",
        "--query_id",
        identity,
        "--workload",
        workload or DATABASE,
        "--max_threads",
        "2",
        "--max_execution_time",
        str(seconds),
        "--max_memory_usage",
        str(memory or (536870912 if read else 2147483648)),
        "--max_bytes_to_read",
        str(1073741824 if read else 0),
        "--max_result_rows",
        str(row_limit),
        "--result_overflow_mode",
        "throw",
        "--use_query_cache",
        "0",
        "--use_query_condition_cache",
        "0",
        "--output_format_json_quote_64bit_integers",
        "0",
        "--format",
        "JSON",
        "--multiquery",
    ]
    started = time.perf_counter()
    result: dict[str, Any] = {"label": label, "query_id": identity, "sql": sql}
    try:
        process = subprocess.run(
            command,
            cwd=HOMELAB,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            timeout=seconds + 30,
        )
        result["exit"] = process.returncode
        if process.returncode:
            result["error"] = process.stderr[:6000]
        elif process.stdout.strip():
            result["response"] = json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        result.update(
            exit=124,
            error="Transport deadline; query may still be active. Inspect and cancel this exact query_id before any replay.",
        )
    result["wall_s"] = time.perf_counter() - started
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with (ARTIFACTS / "operations.jsonl").open("a") as output:
        output.write(json.dumps(result) + "\n")
    return result


def require(sql: str, **kwargs: Any) -> dict[str, Any]:
    result = execute(sql, **kwargs)
    if result["exit"]:
        raise RuntimeError(result["error"])
    return result


def data(sql: str, **kwargs: Any) -> list[dict[str, Any]]:
    return require(sql, **kwargs)["response"]["data"]


def target(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("Invalid isolated target")
    return f"{DATABASE}.{name}"


def literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
