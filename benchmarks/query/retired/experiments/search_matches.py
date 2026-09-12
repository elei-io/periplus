"""Read-only prototype: body discovery followed by exact text-node provenance.

Not installed in the public catalogue. Run within a caller-owned snapshot/deadline.
"""

from time import perf_counter

import icu

from periplus.materialization.dom.nodes import NodeRow
from periplus.materialization.search_text import body_parts
from periplus.query.benchmarking import bounded_rows

MAX_BODY_CHARS = 500_000
MAX_NODES = 100_000
MAX_MATCHES = 3
SNIPPET_CHARS = 240
NORMALIZE_QUERY = "lower(nfc_normalize(trim(regexp_replace(coalesce(?, ''), '[\\t\\n\\r\\f\\x0b ]+', ' ', 'g'))))"


def matched_snippets(db, nodes, query, *, expected_prose=None):
    """Map normalization segments to owners, never infer spans from postings."""
    if query is not None and len(query) > 256:
        raise ValueError("search query must be at most 256 characters")
    needle = db.execute("SELECT " + NORMALIZE_QUERY, [query]).fetchone()[0]
    if not needle:
        return []
    chars, owners = [], []
    pending_space = False
    for text, owner in body_parts(nodes):
        for char in text:
            if char.isspace():
                pending_space = bool(chars)
                continue
            if pending_space:
                chars.append(" ")
                owners.append(None)
                pending_space = False
            chars.append(char)
            owners.append(owner)
            if len(chars) > MAX_BODY_CHARS:
                raise ValueError("prototype body character budget exceeded")
    prose = "".join(chars)
    if expected_prose is not None and prose != expected_prose:
        raise ValueError(
            "stored prose differs from reconstructed body; refusing false provenance"
        )
    if not prose:
        return []
    normalizer = icu.Normalizer2.getNFCInstance()
    display, provenance = [], []
    segment, source_owners = [], set()

    def flush():
        if segment:
            normalized = normalizer.normalize("".join(segment))
            display.extend(normalized)
            provenance.extend([tuple(sorted(source_owners))] * len(normalized))
            segment.clear()
            source_owners.clear()

    for char, owner in zip(chars, owners, strict=True):
        if normalizer.hasBoundaryBefore(char):
            flush()
        segment.append(char)
        if owner is not None:
            source_owners.add(owner)
    flush()
    # Ask the same DuckDB engine used by discovery for its exact lowercase map.
    # Python/ICU case folding would change matching (e.g. sharp s, dotted I).
    lower_map = dict(
        db.execute(
            "SELECT ch, lower(ch) FROM unnest(?) AS t(ch)", [sorted(set(display))]
        ).fetchall()
    )
    folded, offsets = [], []
    for index, char in enumerate(display):
        lowered = lower_map[char]
        folded.append(lowered)
        offsets.extend([index] * len(lowered))
    display = "".join(display)
    folded = "".join(folded)
    expected = db.execute("SELECT lower(nfc_normalize(?))", [prose]).fetchone()[0]
    if folded != expected:
        raise ValueError("engine normalization differs from provenance mapping")
    results, seen = [], set()
    offset = 0
    while len(results) < MAX_MATCHES:
        found = folded.find(needle, offset)
        if found < 0:
            break
        start, end = offsets[found], offsets[found + len(needle) - 1] + 1
        indexes = sorted({owner for group in provenance[start:end] for owner in group})
        snippet_start = max(start - 60, 0)
        snippet = display[snippet_start : snippet_start + SNIPPET_CHARS]
        identity = (snippet, tuple(indexes))
        if identity not in seen:
            results.append({"snippet": snippet, "node_indexes": indexes})
            seen.add(identity)
        offset = found + len(needle)
    return results


def prototype_search(db, query, *, limit=10):
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if query is not None and len(query) > 256:
        raise ValueError("search query must be at most 256 characters")
    started = perf_counter()
    sql = f"""WITH args AS (SELECT {NORMALIZE_QUERY} AS needle)
        SELECT p.content_sha256, p.text FROM material.prose p, args
        WHERE needle <> '' AND contains(lower(nfc_normalize(p.text)), needle)
          AND EXISTS (SELECT 1 FROM public_v1.capture c WHERE c.content_id=p.content_sha256)
        ORDER BY p.content_sha256 LIMIT ?"""
    candidates = bounded_rows(db.execute(sql, [query, limit]))
    discovery_seconds = perf_counter() - started
    results, node_rows = [], 0
    for content_id, prose in candidates:
        if len(prose) > MAX_BODY_CHARS:
            raise ValueError("prototype body character budget exceeded")
        rows = bounded_rows(
            db.execute(
                """SELECT node_index,parent_index,subtree_end_index,
            sibling_index,node_type,name,namespace,value,depth FROM material.html_nodes
            WHERE content_sha256=? ORDER BY node_index LIMIT ?""",
                [content_id, MAX_NODES + 1],
            ),
            max_rows=MAX_NODES + 1,
        )
        if len(rows) > MAX_NODES:
            raise ValueError("prototype node budget exceeded")
        nodes = tuple(NodeRow(*row) for row in rows)
        node_rows += len(nodes)
        matches = matched_snippets(db, nodes, query, expected_prose=prose)
        if not matches:
            raise ValueError("discovery hit has no exact node-backed match")
        results.append({"content_id": content_id, "matches": matches, "score": 1.0})
    return results, {
        "discovery_seconds": discovery_seconds,
        "total_seconds": perf_counter() - started,
        "contents": len(results),
        "nodes_read": node_rows,
    }
