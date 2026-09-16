"""Measure repeated vocabulary units separately from their occurrence counts."""

from __future__ import annotations

import json

from client import ARTIFACTS, data, target


def measure(scale: int) -> dict:
    element = target("elements_lean")
    document = target("source_docs")
    queries = {
        "class_tokens": f"SELECT count() occurrences,uniqExact(token) distinct_units FROM {element} ARRAY JOIN class_tokens AS token WHERE sample_rank<={scale}",
        "class_combinations": f"SELECT count() occurrences,uniqExact(attributes['class']) distinct_units FROM {element} WHERE sample_rank<={scale} AND attributes['class']!=''",
        "attribute_names": f"SELECT count() occurrences,uniqExact(token) distinct_units FROM {element} ARRAY JOIN mapKeys(attributes) AS token WHERE sample_rank<={scale}",
        "tags": f"SELECT count() occurrences,uniqExact(tag) distinct_units FROM {element} WHERE sample_rank<={scale}",
        # Includes script/style tokens. Approximate cardinality keeps dictionary
        # measurement itself bounded; it is not an English vocabulary claim.
        "document_tokens": f"SELECT count() occurrences,uniqCombined64(token) approximate_distinct_units FROM {document} ARRAY JOIN splitByNonAlpha(lower(document_text)) AS token WHERE sample_rank<={scale} AND token!='' SETTINGS max_block_size=4",
    }
    results = {
        name: data(sql, read=False, seconds=300)[0] for name, sql in queries.items()
    }
    result = {"scale": scale, "vocabularies": results}
    with (ARTIFACTS / "vocabularies.jsonl").open("a") as output:
        output.write(json.dumps(result) + "\n")
    return result


if __name__ == "__main__":
    for size in [200, 2000, 20000]:
        print(json.dumps(measure(size)), flush=True)
