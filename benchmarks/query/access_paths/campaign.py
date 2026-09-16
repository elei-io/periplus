"""Run the repeatable comparison on a freshly initialized private database.

Create the source snapshot with setup.py and the probe pod with probe.py first.
This deliberately stops on uncertain writes; inspect state before resuming.
"""

from client import data, require, target
from experiment import load_range, public_queries, run_queries
from native_followups import lean_full_elements_ddl
from probe import run as probe

NATIVE = [
    "docs_plain",
    "docs_indexed_packed",
    "elements_plain",
    "elements_compact",
    "elements_lean",
    "captures_none",
    "captures_light",
    "captures_cover",
    "json_plain",
    "json_indexed",
    "json_name",
]


def run() -> None:
    if data(f"SELECT count() AS n FROM {target('source_docs')}")[0]["n"] != 20000:
        raise ValueError(
            "The fixed-scale campaign requires the verified 20,000-document fixture"
        )
    low = 0
    for high in [200, 2000, 20000]:
        for layout in NATIVE:
            load_range(low, high, [layout])
            run_queries(high, [layout], 2)
        if high <= 2000:
            load_range(low, high, ["docs_indexed"])
            run_queries(high, ["docs_indexed"], 2)
            probe("worker", low, high, "elements_full", 32)
            run_queries(high, ["elements_full"], 2)
            public_queries(high, ["elements_full"], 2)
        probe("worker", low, high, "elements_full_lean", 96)
        run_queries(high, ["elements_full_lean"], 2)
        public_queries(
            high,
            [name for name in NATIVE if not name.startswith("json_")]
            + ["elements_full_lean"],
            2,
        )
        low = high
    require(
        lean_full_elements_ddl().replace(
            target("elements_full_lean"), target("elements_raw_probe")
        ),
        read=False,
    )
    probe("parser")
    probe("raw")


if __name__ == "__main__":
    run()
