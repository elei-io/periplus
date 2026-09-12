"""Read-only, same-snapshot vocabulary/postings discovery experiment."""

from dataclasses import asdict
from pathlib import Path
import json
import re

from periplus.query.benchmarking import QueryCase, _measure, bounded_rows


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def queries(query, anchor):
    if len(query) > 256:
        raise ValueError("search query must be at most 256 characters")
    if not re.fullmatch("[a-z]{3,}", anchor) or anchor not in query:
        raise ValueError(
            "experiment requires a lowercase ASCII word anchor in the query"
        )
    q, a = literal(query), literal(anchor)
    predicate = f"contains(lower(nfc_normalize(p.text)), {q})"
    captured = (
        "EXISTS (SELECT 1 FROM public_v1.capture c WHERE c.content_id=p.content_sha256)"
    )
    vocabulary = (
        f"SELECT term_id FROM material.term WHERE contains(text, {a}) ORDER BY term_id"
    )
    baseline = f"SELECT p.content_sha256 AS content_id FROM material.prose p WHERE {predicate} AND {captured} ORDER BY content_id"
    candidate = f"""WITH terms AS MATERIALIZED ({vocabulary}),
        candidates AS MATERIALIZED (
          SELECT DISTINCT p.content_sha256 FROM material.content_posting p JOIN terms t USING(term_id)
        ), selected AS MATERIALIZED (
          SELECT p.content_sha256,p.text FROM material.prose p SEMI JOIN candidates c USING(content_sha256)
        ) SELECT p.content_sha256 AS content_id FROM selected p WHERE {predicate} AND {captured} ORDER BY content_id"""
    return baseline, vocabulary, candidate


def run(db, query="robot", anchor="robot"):
    baseline, vocabulary, candidate = queries(query, anchor)
    measurements = {}

    def measure(name, sql):
        print(json.dumps({"stage": name}), flush=True)
        case = QueryCase(
            name,
            name,
            "Vocabulary discovery",
            "both",
            True,
            (None,),
            "4GiB",
            60000,
            sql,
            Path("/tmp"),
            seconds=120,
        )
        result = asdict(_measure(db, case, None, 1))
        measurements[name] = result
        print(json.dumps({"measurement": name, "result": result}), flush=True)
        return result

    reference = measure("baseline", baseline)
    measure("vocabulary", vocabulary)
    ids = [r[0] for r in bounded_rows(db.execute(vocabulary), max_rows=10000)]
    exact = bounded_rows(
        db.execute("SELECT term_id FROM material.term WHERE text=?", [anchor])
    )
    if exact:
        measure(
            "exact-term-postings",
            f"SELECT DISTINCT content_sha256 AS content_id FROM material.content_posting WHERE term_id={int(exact[0][0])} ORDER BY content_id",
        )
    posting_sql = (
        "SELECT DISTINCT content_sha256 AS content_id FROM material.content_posting WHERE term_id IN ("
        + (",".join(str(int(i)) for i in ids) or "NULL")
        + ") ORDER BY content_id"
    )
    measure("expanded-postings", posting_sql)
    keys = [r[0] for r in bounded_rows(db.execute(posting_sql))]
    verified_sql = f"""WITH selected AS MATERIALIZED (
        SELECT content_sha256,text FROM material.prose WHERE content_sha256 IN ({",".join(literal(k) for k in keys) or "NULL"})
        ) SELECT p.content_sha256 AS content_id FROM selected p
        WHERE contains(lower(nfc_normalize(p.text)), {literal(query)})
          AND EXISTS (SELECT 1 FROM public_v1.capture c WHERE c.content_id=p.content_sha256)
        ORDER BY content_id"""
    verified = measure("literal-key-verification", verified_sql)
    composed = measure("composed", candidate)
    reverse = measure("baseline-reverse", baseline)
    assert (
        reference["result_digest"]
        == verified["result_digest"]
        == composed["result_digest"]
        == reverse["result_digest"]
    ), "candidate changed complete results"
    summary = {
        "query": query,
        "vocabulary_terms": len(ids),
        "candidate_contents": len(keys),
        "matched_contents": reference["result_rows"],
        "equivalent": True,
        "measurements": measurements,
    }
    print(json.dumps({"summary": summary}), flush=True)
    return summary


def indexed_matches(db, query, anchor, *, limit=100):
    """Prototype bounded output with exact node-backed verification after lookup."""
    from time import perf_counter
    from search_matches import MAX_NODES, NodeRow, matched_snippets

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    _, vocabulary, _ = queries(query, anchor)
    started = perf_counter()
    ids = [r[0] for r in bounded_rows(db.execute(vocabulary), max_rows=10000)]
    vocabulary_seconds = perf_counter() - started
    candidate_sql = f"""SELECT DISTINCT p.content_sha256 FROM material.content_posting p
        WHERE term_id IN ({",".join(str(int(i)) for i in ids) or "NULL"})
          AND EXISTS (SELECT 1 FROM public_v1.capture c WHERE c.content_id=p.content_sha256)
        ORDER BY p.content_sha256"""
    keys = [r[0] for r in bounded_rows(db.execute(candidate_sql))]
    discovery_seconds = perf_counter() - started
    results, checked, node_rows = [], 0, 0
    for key in keys:
        rows = bounded_rows(
            db.execute(
                """SELECT node_index,parent_index,subtree_end_index,
            sibling_index,node_type,name,namespace,value,depth FROM material.html_nodes
            WHERE content_sha256=? ORDER BY node_index LIMIT ?""",
                [key, MAX_NODES + 1],
            ),
            max_rows=MAX_NODES + 1,
        )
        if len(rows) > MAX_NODES:
            raise ValueError("prototype node budget exceeded")
        checked += 1
        node_rows += len(rows)
        matches = matched_snippets(db, tuple(NodeRow(*row) for row in rows), query)
        if matches:
            results.append({"content_id": key, "matches": matches, "score": 1.0})
        if len(results) == limit:
            break
    return results, {
        "vocabulary_seconds": vocabulary_seconds,
        "discovery_seconds": discovery_seconds,
        "total_seconds": perf_counter() - started,
        "candidate_contents": len(keys),
        "checked_contents": checked,
        "contents": len(results),
        "nodes_read": node_rows,
    }
