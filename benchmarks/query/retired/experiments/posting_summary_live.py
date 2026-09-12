import json
from dataclasses import asdict
from pathlib import Path

from periplus.platform.catalogue import catalogue_from_env
from periplus.query.benchmarking import QueryCase, _measure


def run():
    with catalogue_from_env(read_only=True) as c:
        d = c.trusted_connection
        d.execute("SET threads=2; SET memory_limit='4GiB'")
        d.execute("BEGIN")
        try:
            for words in [("microcontroller",), ("robot",), ("the",), ("robot", "the")]:
                ids = [
                    r[0]
                    for r in d.execute(
                        "SELECT term_id FROM material.term WHERE text IN ("
                        + ",".join("?" for _ in words)
                        + ")",
                        words,
                    ).fetchall()
                ]
                assert len(ids) == len(words)
                results = []
                for table in [
                    "content_posting",
                    "node_posting",
                    "node_posting",
                    "content_posting",
                ]:
                    sql = f"WITH hits AS (SELECT content_sha256 FROM material.{table} WHERE term_id IN ({','.join(map(str, ids))}) GROUP BY content_sha256 HAVING count(DISTINCT term_id)={len(ids)}) SELECT count(*)::BIGINT AS contents,bit_xor(hash(content_sha256)) AS digest FROM hits"
                    case = QueryCase(
                        "posting-summary",
                        "Posting summary",
                        "Discovery",
                        "schema",
                        True,
                        (None,),
                        "4GiB",
                        60000,
                        sql,
                        Path("/tmp"),
                        seconds=120,
                    )
                    print(json.dumps({"start": table, "words": words}), flush=True)
                    r = asdict(_measure(d, case, None, 1))
                    results.append(r)
                    print(
                        json.dumps({"table": table, "words": words, "measurement": r}),
                        flush=True,
                    )
                assert len({r["result_digest"] for r in results}) == 1
            print(json.dumps({"complete": True}), flush=True)
        finally:
            d.execute("ROLLBACK")


if __name__ == "__main__":
    run()
