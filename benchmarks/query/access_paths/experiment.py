"""Run native layout experiments against an explicitly isolated database."""

from __future__ import annotations

import argparse
import json
import time
from hashlib import sha256
from typing import Any

from client import ARTIFACTS, DATABASE, data, execute, literal, target
from load import capture_insert, document_insert, element_insert, json_insert
from queries import capture_cases, document_cases, element_cases, json_cases

LAYOUTS = [
    "docs_plain",
    "docs_indexed",
    "elements_plain",
    "elements_compact",
    "elements_full",
    "captures_none",
    "captures_light",
    "captures_cover",
    "json_plain",
    "json_indexed",
    "json_name",
    "docs_indexed_packed",
    "elements_lean",
    "elements_full_lean",
    "captures_keyword",
]


def load_range(low: int, high: int, selected: list[str]) -> None:
    for name in selected:
        if data(
            f"SELECT count() AS n FROM {target(name)} WHERE sample_rank>{low} AND sample_rank<={high}"
        )[0]["n"]:
            raise ValueError(
                f"{name} target range is not empty; inspect partial state before replay"
            )
        if name.startswith("docs_"):
            sql = document_insert(name, low, high)
        elif name.startswith("elements_"):
            sql = element_insert(name, low, high, name.startswith("elements_full"))
        elif name.startswith("captures_"):
            sql = capture_insert(name, low, high)
        else:
            sql = json_insert(name, low, high)
        if " SETTINGS " not in sql:
            sql += " SETTINGS "
        else:
            sql += ","
        sql += "min_insert_block_size_bytes=33554432,min_insert_block_size_rows=0"
        result = execute(
            sql, label=f"load:{name}:{low}:{high}", read=False, seconds=600
        )
        print(
            json.dumps(
                {k: result.get(k) for k in ["label", "exit", "wall_s", "error"]}
            ),
            flush=True,
        )
        if result["exit"]:
            raise SystemExit(
                "Inspect partial target before retry; inserts are not blindly replayed"
            )


def digest(result: dict[str, Any]) -> str | None:
    if "response" not in result:
        return None
    return sha256(
        json.dumps(
            result["response"]["data"],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def run_queries(scale: int, selected: list[str], runs: int) -> None:
    url = data(
        f"SELECT url FROM {target('source_captures')} WHERE sample_rank=1 LIMIT 1"
    )[0]["url"]
    for name in selected:
        if name.startswith("docs_"):
            cases = document_cases(name)
        elif name.startswith("elements_"):
            cases = element_cases(name, name.startswith("elements_full"))
        elif name.startswith("captures_"):
            cases = capture_cases(name, url)
        else:
            cases = json_cases(name, "Blueair 3450i")
        run_cases(scale, name, cases, runs)
    snapshot = data(
        f"SELECT table, sum(rows) rows,sum(bytes_on_disk) bytes,sum(data_compressed_bytes) compressed,count() parts FROM system.parts WHERE database={literal(DATABASE)} AND active GROUP BY table ORDER BY table"
    )
    with (ARTIFACTS / "storage.jsonl").open("a") as output:
        output.write(
            json.dumps({"scale": scale, "at": time.time(), "tables": snapshot}) + "\n"
        )


def run_cases(scale: int, name: str, cases: dict[str, str], runs: int = 2) -> None:
    for case, sql in cases.items():
        plan = execute("EXPLAIN indexes=1 " + sql, label=f"plan:{name}:{case}:{scale}")
        for run in range(runs):
            result = execute(
                sql, label=f"query:{name}:{case}:{scale}:{run}", seconds=20
            )
            row = {
                "scale": scale,
                "layout": name,
                "case": case,
                "run": run,
                "query_id": result["query_id"],
                "exit": result["exit"],
                "wall_s": result["wall_s"],
                "digest": digest(result),
                "rows": result.get("response", {}).get("rows"),
                "error": result.get("error"),
                "plan_query_id": plan["query_id"],
            }
            with (ARTIFACTS / "results.jsonl").open("a") as output:
                output.write(json.dumps(row) + "\n")
            print(json.dumps(row), flush=True)
            if result["exit"]:
                break


def public_queries(scale: int, selected: list[str], runs: int) -> None:
    from public_cases import cases, link_case, views

    url = data(
        f"SELECT url FROM {target('source_captures')} WHERE sample_rank=1 LIMIT 1"
    )[0]["url"]
    for name in selected:
        if name.startswith("json_"):
            continue
        if name.startswith("captures_"):
            acceptance = {"public_selected_links": link_case(name, url)}
        else:
            views(name)
            acceptance = cases(name)
        run_cases(scale, name, acceptance, runs)


def collect_metrics() -> None:
    operations = [
        json.loads(line)
        for line in (ARTIFACTS / "operations.jsonl").read_text().splitlines()
    ]
    identities = {
        row["query_id"]
        for row in operations
        if row["label"].split(":")[0]
        in ["query", "load", "plan", "mv", "merge", "bypass", "profile", "fanout_load"]
    }
    for path in [*ARTIFACTS.glob("worker-*.jsonl"), ARTIFACTS / "raw-probe.jsonl"]:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("query_id"):
                identities.add(row["query_id"])
    # Early probes predate explicit reader-ID logging. Recover only this private
    # source's exact HTTP query shape, retaining the query text for audit.
    reader_prefix = f"SELECT lower(hex(document_id)) AS document_id,sample_rank,document_text,elements FROM {target('source_docs')} WHERE sample_rank>"
    readers = data(
        f"SELECT query_id,query FROM system.query_log WHERE startsWith(query,{literal(reader_prefix)}) AND startsWith(query_id,'access-') AND interface=2 AND type!='QueryStart'",
        read=False,
        row_limit=10000,
    )
    (ARTIFACTS / "worker-readers.json").write_text(json.dumps(readers, indent=2))
    identities.update(row["query_id"] for row in readers)
    ids = ",".join(literal(value) for value in sorted(identities))
    sql = f"""SELECT query_id,type,query_duration_ms,read_rows,read_bytes,written_rows,written_bytes,result_rows,memory_usage,exception_code,exception,projections,ProfileEvents
    FROM system.query_log WHERE query_id IN ({ids}) AND type!='QueryStart' ORDER BY query_start_time_microseconds"""
    result = data(sql, read=False, row_limit=10000)
    (ARTIFACTS / "metrics.json").write_text(json.dumps(result, indent=2))
    print(f"{len(result)} log records for {len(ids.split(','))} operations")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["load", "query", "public", "metrics"])
    p.add_argument("--scale", type=int, default=200)
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--layout", action="append", choices=LAYOUTS)
    p.add_argument("--runs", type=int, default=2)
    args = p.parse_args()
    selected = args.layout or LAYOUTS
    if args.action == "load":
        load_range(args.after, args.scale, selected)
    elif args.action == "query":
        run_queries(args.scale, selected, args.runs)
    elif args.action == "public":
        public_queries(args.scale, selected, args.runs)
    else:
        collect_metrics()


if __name__ == "__main__":
    main()
