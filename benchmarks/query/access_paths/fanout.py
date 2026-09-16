"""Hold row count fixed; vary overlapping parts using real document identities.

A midpoint key avoids the primary corpus anchor's artificially high hash. Only
these small diagnostic tables have merges stopped, and cleanup removes them.
"""

from __future__ import annotations

import json

from client import ARTIFACTS, BYPASS_CACHES, DATABASE, data, literal, require, target
from experiment import run_cases


def ddl(name: str, kind: str) -> str:
    index = (
        ", INDEX exact document_id TYPE text(tokenizer=array)"
        if kind == "text"
        else ", INDEX membership document_id TYPE bloom_filter(0.001) GRANULARITY 1"
        if kind == "bloom"
        else ""
    )
    partition = (
        " PARTITION BY cityHash64(document_id)%64" if kind == "partition" else ""
    )
    return f"CREATE TABLE {target(name)} (document_id FixedString(64),sample_rank UInt32{index}) ENGINE=MergeTree{partition} ORDER BY document_id"


def run() -> None:
    anchor = data(
        f"SELECT lower(hex(document_id)) AS id FROM {target('source_docs')} WHERE sample_rank=10000"
    )[0]["id"]
    for count in [2, 20, 100]:
        for kind in ["plain", "bloom", "text"]:
            name = f"keys_{kind}_{count}"
            require(ddl(name, kind), read=False)
            require(f"SYSTEM STOP MERGES {target(name)}", read=False)
            for bucket in range(count):
                require(
                    f"INSERT INTO {target(name)} SELECT lower(hex(document_id)),sample_rank FROM {target('source_docs')} WHERE sample_rank%{count}={bucket}",
                    label=f"fanout_load:{name}:{bucket}",
                    read=False,
                )
            query = f"SELECT document_id FROM {target(name)} WHERE document_id={literal(anchor)}"
            run_cases(
                20000,
                name,
                {
                    "identity": query,
                    "identity_bypass": query + " SETTINGS " + BYPASS_CACHES,
                },
                2,
            )
    name = "keys_partitioned"
    require(ddl(name, "partition"), read=False)
    require(
        f"INSERT INTO {target(name)} SELECT lower(hex(document_id)),sample_rank FROM {target('source_docs')}",
        read=False,
    )
    query = (
        f"SELECT document_id FROM {target(name)} WHERE document_id={literal(anchor)}"
    )
    run_cases(
        20000,
        name,
        {
            "identity": query,
            "identity_bypass": query + " SETTINGS " + BYPASS_CACHES,
        },
        2,
    )
    result = data(
        f"SELECT table,count() parts,sum(rows) rows FROM system.parts WHERE database={literal(DATABASE)} AND startsWith(table,'keys_') AND active GROUP BY table ORDER BY table"
    )
    (ARTIFACTS / "fanout-parts.json").write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    run()
