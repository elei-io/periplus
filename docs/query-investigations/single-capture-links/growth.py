"""Compare fixed-capture access as unrelated real link data grows in disposable lakes."""

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
from time import perf_counter

import duckdb
from periplus.query.benchmarking import QueryCase, _measure, deadline

KEY = "075dc086-adb8-499c-8796-589ee28136e5"
SETTINGS = {"threads": "2", "memory_limit": "4GiB", "max_temp_directory_size": "90GB"}
ORDERS = {
    "source": "source_url, target_url, observed_at, occurrence_id",
    "capture": "visit_id, target_url, observed_at, occurrence_id",
}


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def connect(root, read_only=False):
    connection = duckdb.connect(config=SETTINGS)
    connection.execute("LOAD ducklake")
    options = "READ_ONLY" if read_only else f"DATA_PATH {literal(root / 'data')}"
    connection.execute(
        f"ATTACH {literal('ducklake:' + str(root / 'metadata.duckdb'))} AS periplus ({options})"
    )
    return connection


def save(root, report):
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")


def build(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {
        "source_snapshot": args.source_snapshot,
        "settings": SETTINGS,
        "builds": {},
        "pairs": [],
    }
    connection = connect(root)
    connection.execute("CREATE SCHEMA periplus.material")
    connection.execute("CREATE SCHEMA periplus.public_v1")
    for fraction in (1, 5, 10):
        # The selected capture is held fixed; only unrelated capture membership grows.
        selection = f"SELECT * FROM read_parquet({literal(args.input.resolve())}) WHERE visit_id=UUID '{KEY}' OR hash(visit_id)%10 < {fraction}"
        with deadline(connection, 120):
            expected = connection.execute(
                f"SELECT count(*), sum(hash(visit_id,source_url,target_url,observed_at,occurrence_id)::HUGEINT) FROM ({selection})"
            ).fetchone()
        for layout, order in ORDERS.items():
            name = f"{layout}_{fraction}"
            folder = root / name
            started = perf_counter()
            with deadline(connection, 120):
                connection.execute(
                    f"COPY ({selection} ORDER BY {order}) TO {literal(folder)} (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880, ROW_GROUPS_PER_FILE 8)"
                )
                connection.execute(
                    f"CREATE TABLE periplus.material.{name} AS {selection} LIMIT 0"
                )
                connection.execute("BEGIN TRANSACTION")
                files = sorted(folder.glob("*.parquet"))
                for file in files:
                    connection.execute(
                        f"CALL ducklake_add_data_files('periplus', '{name}', {literal(file)}, schema => 'material')"
                    )
                connection.execute("COMMIT")
                observed = connection.execute(
                    f"SELECT count(*), sum(hash(visit_id,source_url,target_url,observed_at,occurrence_id)::HUGEINT) FROM periplus.material.{name}"
                ).fetchone()
                assert observed == expected
            report["builds"][name] = {
                "rows": expected[0],
                "fingerprint": str(expected[1]),
                "files": len(files),
                "bytes": sum(f.stat().st_size for f in files),
                "seconds": perf_counter() - started,
            }
            save(root, report)
            print(json.dumps({name: report["builds"][name]}), flush=True)
    connection.close()


def measure(args):
    root = args.output.resolve()
    os.environ["PERIPLUS_DUCKLAKE_ALIAS"] = "periplus"
    os.environ["PERIPLUS_DUCKLAKE_METADATA_PATH"] = str(root / "metadata.duckdb")
    os.environ["PERIPLUS_DUCKLAKE_DATA_PATH"] = str(root / "data")
    report = json.loads((root / "report.json").read_text())
    expected_digest = None
    for fraction in (1, 5, 10):
        for order in [("source", "capture"), ("capture", "source")]:
            connection = connect(root, read_only=True)
            connection.execute("BEGIN TRANSACTION")
            pair = {"source_url_filter": args.source_url}
            for layout in order:
                name = f"{layout}_{fraction}"
                predicate = f"visit_id=UUID '{KEY}'"
                if args.source_url:
                    predicate += f" AND source_url={literal(args.source_url)}"
                sql = f"SELECT target_url, count(*) AS occurrences FROM periplus.material.{name} WHERE {predicate} GROUP BY target_url ORDER BY occurrences DESC, target_url LIMIT 50"
                case = QueryCase(
                    "growth-lookup",
                    "Fixed capture lookup",
                    "Unrelated corpus growth",
                    "unclassified",
                    True,
                    (None,),
                    "4GiB",
                    5000,
                    sql,
                    root,
                    60,
                )
                result = asdict(_measure(connection, case, None, 2))
                expected_digest = expected_digest or result["result_digest"]
                assert result["result_digest"] == expected_digest
                pair[name] = result
                print(
                    json.dumps(
                        {
                            "table": name,
                            "normal_ms": result["normal_ms"],
                            "warm_ms": result["warm_ms"],
                            "files": [s["files_read"] for s in result["scans"]],
                        }
                    ),
                    flush=True,
                )
            report["pairs"].append(pair)
            save(root, report)
            connection.execute("ROLLBACK")
            connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["build", "measure"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-snapshot", type=int, required=True)
    parser.add_argument(
        "--source-url", help="Add the known normalized source key during measurement."
    )
    arguments = parser.parse_args()
    (build if arguments.phase == "build" else measure)(arguments)
