"""Independent positive oracles, row completeness and cross-layout equivalence.

Diagnostics may scan beyond the query acceptance budget. They are never counted
as performance passes. Unordered previews are checked against source rows, not
against another layout's arbitrary first ten rows.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any

from client import ARTIFACTS, data, literal, target
from queries import DOCUMENT
from source import verify


def normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        converted = {key.rsplit(".", 1)[-1]: value for key, value in row.items()}
        if len(converted) != len(row):
            raise ValueError("Ambiguous qualified result columns")
        output.append(converted)
    return output


def fingerprint(rows: list[dict[str, Any]]) -> str:
    return sha256(
        json.dumps(normalized(rows), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def expected_elements(document: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for element in document["elements"]:
        result.append(
            {
                "document_id": document["document_id"],
                **{
                    key: value
                    for key, value in element.items()
                    if key not in {"text_start", "text_end"}
                },
                "text": document["document_text"][
                    element["text_start"] : element["text_end"]
                ],
            }
        )
    return result


def run() -> None:
    verification = {"source": verify()}
    operations = {
        row["query_id"]: row
        for row in map(
            json.loads, (ARTIFACTS / "operations.jsonl").read_text().splitlines()
        )
    }
    results = list(
        map(json.loads, (ARTIFACTS / "results.jsonl").read_text().splitlines())
    )
    aliases = {
        "class_element_expression": "class_element",
        "public_class": "class_element",
        "class_same_element_expression": "class_same_element",
        "attribute_id_scalar": "attribute_id",
        "attribute_id_projection": "attribute_id",
        "public_selected_headings": "selected_headings",
        "public_selected_links": "selected_links",
        "public_count": "element_count",
    }
    groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        case = aliases.get(result["case"], result["case"])
        if result["exit"] or "preview" in case or result["layout"].startswith("keys_"):
            continue
        groups[(result["scale"], case)].append(result)
    compared = []
    for (scale, case), rows in groups.items():
        signatures = {
            fingerprint(operations[row["query_id"]]["response"]["data"]) for row in rows
        }
        assert len(signatures) == 1, (
            scale,
            case,
            [(r["layout"], r["query_id"]) for r in rows],
        )
        compared.append(
            {
                "scale": scale,
                "case": case,
                "successful_runs": len(rows),
                "digest": next(iter(signatures)),
            }
        )
    verification["equivalence"] = compared

    documents = data(
        f"SELECT lower(hex(document_id)) AS document_id,document_text,elements FROM {target('source_docs')} WHERE sample_rank IN (1,59,162) SETTINGS max_block_size=1",
        read=False,
    )
    canonical = {doc["document_id"]: expected_elements(doc) for doc in documents}
    anchor = canonical[DOCUMENT]
    classes = [
        {"document_id": DOCUMENT, "node_index": row["node_index"]}
        for row in anchor
        if "hp-icon" in row["attributes"].get("class", "").split()
    ]
    assert [row["node_index"] for row in classes] == [468, 504, 540]
    headings = [
        {key: row[key] for key in ["document_id", "node_index", "text"]}
        for row in anchor
        if row["tag"] in {"h1", "h2", "h3"}
    ]
    captures = data(
        f"SELECT links FROM {target('source_captures')} WHERE sample_rank=1 ORDER BY captured_at DESC,capture_id DESC LIMIT 1"
    )
    counts = Counter(link["target_url"] for link in captures[0]["links"])
    links = [
        {"target_url": url, "occurrences": count}
        for url, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[
            :50
        ]
    ]
    for result in results:
        if result["exit"]:
            continue
        actual = normalized(operations[result["query_id"]]["response"]["data"])
        case = aliases.get(result["case"], result["case"])
        if case == "class_element":
            assert actual == classes, (result["layout"], result["scale"])
        elif case == "selected_headings":
            assert actual == headings
        elif case == "selected_links":
            assert actual == links
        elif case == "class_cross_element_negative":
            assert actual == []
    verification["independent_oracles"] = {
        "classes": len(classes),
        "headings": len(headings),
        "links": len(links),
        "cross_element_negative": 0,
    }
    json_expected = []
    for document in documents:
        for element in document["elements"]:
            if (
                element["tag"] != "script"
                or element["attributes"].get("type") != "application/ld+json"
            ):
                continue
            try:
                value = json.loads(element["text_direct"])
            except (ValueError, TypeError):
                continue
            if (
                isinstance(value, dict)
                and value.get("@type") == "Product"
                and value.get("name") == "Blueair 3450i"
            ):
                json_expected.append(
                    {
                        "document_id": document["document_id"],
                        "node_index": element["node_index"],
                        "raw": element["text_direct"],
                    }
                )
    assert len(json_expected) == 1
    for result in results:
        if not result["exit"] and result["case"] == "json_product":
            assert (
                normalized(operations[result["query_id"]]["response"]["data"])
                == json_expected
            )
    preview_rows = [
        row
        for result in results
        if not result["exit"] and "preview" in result["case"]
        for row in normalized(operations[result["query_id"]]["response"]["data"])
    ]
    missing_ids = {row["document_id"] for row in preview_rows} - set(canonical)
    if missing_ids:
        ids = ",".join(literal(identity) for identity in sorted(missing_ids))
        extra = data(
            f"SELECT lower(hex(d.document_id)) AS document_id,d.document_text,d.elements FROM {target('source_docs')} d WHERE lower(hex(d.document_id)) IN ({ids}) SETTINGS max_block_size=1",
            read=False,
        )
        canonical.update({doc["document_id"]: expected_elements(doc) for doc in extra})
    for row in preview_rows:
        expected = next(
            item
            for item in canonical[row["document_id"]]
            if item["node_index"] == row["node_index"]
        )
        assert row == expected
    verification["independent_oracles"].update(
        json_product=1, preview_rows=len(preview_rows)
    )
    fields = ",".join(anchor[0])
    complete = []
    for name in ["elements_full", "elements_full_lean"]:
        for document in documents:
            identity = document["document_id"]
            expected = canonical[identity]
            actual = data(
                f"SELECT {fields} FROM {target(name)} WHERE document_id={literal(identity)} ORDER BY node_index",
                read=False,
                row_limit=150000,
            )
            assert normalized(actual) == expected, (name, identity)
            complete.append(
                {"table": name, "document_id": identity, "elements": len(expected)}
            )
    verification["full_field_checks"] = complete

    row_checks = []
    for name, scale in [
        ("elements_plain", 20000),
        ("elements_compact", 20000),
        ("elements_lean", 20000),
        ("elements_full_lean", 20000),
        ("elements_full", 2000),
        ("elements_raw_probe", 100),
    ]:
        sql = f"""SELECT count() documents,countIf(a.n!=b.n OR a.n!=a.distinct_nodes OR a.digest!=b.digest) mismatches
        FROM (SELECT document_id,count() n,uniqExact(node_index) distinct_nodes,groupBitXor(cityHash64(node_index)) digest FROM {target(name)} GROUP BY document_id) a
        FULL OUTER JOIN (SELECT lower(hex(document_id)) document_id,length(elements) n,arrayReduce('groupBitXor',arrayMap(e->cityHash64(e.node_index),elements)) digest FROM {target("source_docs")} WHERE sample_rank<={scale}) b USING(document_id)
        SETTINGS max_block_size=4"""
        check = data(sql, read=False, seconds=300)[0]
        assert check == {"documents": scale, "mismatches": 0}, (name, check)
        row_checks.append({"table": name, "scale": scale, **check})
    verification["all_document_node_identities"] = row_checks
    (ARTIFACTS / "correctness.json").write_text(json.dumps(verification, indent=2))
    print(
        json.dumps(
            {
                "equivalence_groups": len(compared),
                "full_field_documents": len(complete),
                "all_document_node_identities": row_checks,
            }
        )
    )


if __name__ == "__main__":
    run()
