"""Native keyword indexes over arbitrary attribute names and values.

Keep Map lookup as the exact predicate: matching a value under the wrong key
must never become a match. The explicit hint is SQL supplied to the engine,
not a Python query rewrite.
"""

from client import require, target
from experiment import load_range, run_cases, run_queries


def cases() -> dict[str, str]:
    relation = target("attributes_probe")
    select = f"SELECT document_id,node_index FROM {relation}"
    predicate = "attributes['viewBox']='0 0 126.719 115.379'"
    order = " ORDER BY document_id,node_index"
    return {
        "attribute_value": f"{select} WHERE {predicate}{order}",
        "attribute_value_without_index": f"{select} WHERE {predicate}{order} SETTINGS use_skip_indexes=0",
        "attribute_value_hint": f"{select} WHERE has(mapValues(attributes),'0 0 126.719 115.379') AND {predicate}{order}",
        "attribute_wrong_key": f"{select} WHERE attributes['id']='0 0 126.719 115.379'{order}",
        "attribute_common_name": f"SELECT count() AS n FROM {relation} WHERE mapContains(attributes,'class')",
    }


def run() -> None:
    low = 0
    for high in [200, 2000, 20000]:
        load_range(low, high, ["captures_keyword"])
        run_queries(high, ["captures_keyword"], 2)
        require(
            f"INSERT INTO {target('attributes_probe')} SELECT document_id,node_index,sample_rank,attributes FROM {target('elements_lean')} WHERE sample_rank>{low} AND sample_rank<={high} SETTINGS min_insert_block_size_bytes=33554432,min_insert_block_size_rows=0",
            label=f"load:attributes_probe:{low}:{high}",
            read=False,
            seconds=600,
        )
        run_cases(high, "attributes_probe", cases(), 2)
        low = high


if __name__ == "__main__":
    run()
