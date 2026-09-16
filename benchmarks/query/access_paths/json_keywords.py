"""Compare exact Product names against the earlier covering name projection."""

from client import require, target
from experiment import load_range, run_queries


def run() -> None:
    require(
        f"""CREATE TABLE {target("json_keyword")} (
        document_id FixedString(64),node_index UInt32,sample_rank UInt32,
        raw String CODEC(ZSTD(3)),types Array(String),name String,
        INDEX types types TYPE text(tokenizer=array),
        INDEX names name TYPE text(tokenizer=array)
    ) ENGINE=MergeTree ORDER BY (document_id,node_index)""",
        read=False,
    )
    low = 0
    for high in [200, 2000, 20000]:
        load_range(low, high, ["json_keyword"])
        run_queries(high, ["json_keyword"], 2)
        low = high


if __name__ == "__main__":
    run()
