"""Query-API search over complete positional postings, inside the request snapshot."""

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import icu
from sqlglot import exp

from periplus.materialization.tokenization import term_tokens
from periplus.query.content_scope import _number_parameters
from periplus.query.validation import _EXPLAIN_PREFIX, _one_statement

RESULT_TYPE = "STRUCT(content_id VARCHAR, matches STRUCT(snippet VARCHAR, node_indexes INTEGER[])[], score DOUBLE)[]"
MAX_TEXT = 8_000_000
MAX_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class SearchQuery:
    terms: tuple[str, ...]
    phrase: bool


def parse_query(value: object) -> SearchQuery:
    if value is None:
        return SearchQuery((), False)
    if not isinstance(value, str):
        raise ValueError("search query must be a string or NULL")  # noqa: TRY004 -- public validation returns a client error
    if len(value) > 256:
        raise ValueError("search query must be at most 256 characters")
    value = value.strip()
    phrase = value.startswith('"') and value.endswith('"') and len(value) >= 2
    if '"' in (value[1:-1] if phrase else value):
        raise ValueError(
            "search supports either plain terms or one fully double-quoted phrase"
        )
    terms = tuple(term_tokens(value[1:-1] if phrase else value))
    if len(terms) > 32:
        raise ValueError("search supports at most 32 query tokens")
    return SearchQuery(terms, phrase)


def _rows(db, sql, parameters=None, *, maximum=100, check=lambda: None):
    cursor = db.execute(sql, parameters)
    result, size = [], 0
    while row := cursor.fetchone():
        check()
        size += len(json.dumps(row, ensure_ascii=False).encode())
        if len(result) >= maximum or size > MAX_BYTES:
            raise ValueError("search intermediate result exceeds its collection budget")
        result.append(row)
    return result


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _snippet(text: str, query: SearchQuery, check=lambda: None) -> str:
    normalizer = icu.Normalizer2.getNFCInstance()
    display = normalizer.normalize(text)
    folded = icu.UnicodeString(display)
    folded.foldCase()
    normalized = icu.UnicodeString(normalizer.normalize(folded))
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(normalized)
    start, target, window = iterator.first(), None, deque(maxlen=len(query.terms))
    for number, end in enumerate(iterator):
        if number % 1024 == 0:
            check()
        if iterator.getRuleStatus() >= 100:
            term = str(normalized[start:end])
            window.append((term, start))
            matched = (
                tuple(t for t, _ in window) == query.terms
                if query.phrase
                else term in query.terms
            )
            if matched:
                target = window[0][1] if query.phrase else start
                break
        start = end
    if target is None:
        raise ValueError("posting provenance does not contain the indexed match")
    if display.isascii():
        offset = target
    else:
        # Map only the prefix needed to locate the match. Memory stays bounded
        # by a normalization segment, rather than one owner per source character.
        units, segment, segment_start, offset = 0, [], 0, 0
        found = False
        for index, char in enumerate(display):
            if index % 4096 == 0:
                check()
            folded_char = icu.UnicodeString(char)
            folded_char.foldCase()
            for ch in str(folded_char):
                if segment and normalizer.hasBoundaryBefore(ch):
                    length = (
                        len(normalizer.normalize("".join(segment)).encode("utf-16-le"))
                        // 2
                    )
                    if units + length > target:
                        offset = segment_start
                        found = True
                        break
                    units += length
                    segment.clear()
                if not segment:
                    segment_start = index
                segment.append(ch)
            if found:
                break
        if not found:
            offset = segment_start
    start = max(0, offset - 60)
    return " ".join(display[start : start + 240].split())


def discover(db, query: SearchQuery, check: Callable[[], None]) -> list[dict]:
    if not query.terms:
        return []
    unique = tuple(dict.fromkeys(query.terms))
    vocabulary = _rows(
        db,
        "SELECT text,term_id FROM material.term WHERE text IN ("
        + ",".join(_literal(t) for t in unique)
        + ")",
        maximum=32,
        check=check,
    )
    ids = dict(vocabulary)
    if len(ids) != len(unique):
        return []
    literals = ",".join(str(int(ids[t])) for t in unique)
    if query.phrase:
        aliases = {term: f"p{i}" for i, term in enumerate(unique)}
        first = aliases[query.terms[0]]
        conditions = (
            " AND ".join(
                f"list_contains({aliases[t]}.positions, x+{i})"
                for i, t in enumerate(query.terms[1:], 1)
            )
            or "true"
        )
        starts = f"list_filter({first}.positions, x -> {conditions})"
        source = f"(SELECT content_sha256,positions FROM material.posting WHERE term_id={int(ids[unique[0]])}) p0"
        for term in unique[1:]:
            source += f" JOIN (SELECT content_sha256,positions FROM material.posting WHERE term_id={int(ids[term])}) {aliases[term]} USING(content_sha256)"
        candidate = f"""WITH hits AS (SELECT p0.content_sha256 AS content_id,{starts} starts FROM {source})
            SELECT content_id,len(starts)::DOUBLE score,list_slice(starts,1,3) starts FROM hits
            WHERE len(starts)>0 AND EXISTS(SELECT 1 FROM public_v1.capture c WHERE c.content_id=hits.content_id)
            ORDER BY score DESC,content_id LIMIT 100"""
    else:
        candidate = f"""SELECT p.content_sha256 AS content_id,sum(frequency)::DOUBLE score,NULL::BIGINT[] starts
            FROM material.posting p WHERE term_id IN ({literals})
            AND EXISTS(SELECT 1 FROM public_v1.capture c WHERE c.content_id=p.content_sha256)
            GROUP BY p.content_sha256 HAVING count(*)={len(unique)} ORDER BY score DESC,content_id LIMIT 100"""
    candidates = _rows(db, candidate, check=check)
    output = []
    for content_id, score, starts in candidates:
        check()
        # Only collect provenance for at most three occurrences per term. Position
        # arrays remain in DuckDB; large common-term postings do not enter Python.
        if query.phrase:
            wanted = "[" + ",".join(str(int(x)) for x in starts) + "]"
            pieces = []
            for offset, term in enumerate(query.terms):
                pieces.append(f"""SELECT s AS occurrence, unnest(node_indexes[list_position(positions,s+{offset})]) AS node_index
                    FROM material.posting,unnest({wanted}::BIGINT[]) a(s)
                    WHERE content_sha256={_literal(content_id)} AND term_id={int(ids[term])}""")
            groups_sql = (
                "SELECT occurrence,list(DISTINCT node_index ORDER BY node_index) FROM ("
                + " UNION ALL ".join(pieces)
                + ") GROUP BY occurrence ORDER BY occurrence"
            )
        else:
            groups_sql = f"""SELECT position,list(DISTINCT node_index ORDER BY node_index) FROM (
                SELECT unnest(list_slice(positions,1,3)) AS position,unnest(list_slice(node_indexes,1,3)) owners
                FROM material.posting WHERE content_sha256={_literal(content_id)} AND term_id IN ({literals})
                ) p,unnest(owners) n(node_index) GROUP BY position ORDER BY position LIMIT 3"""
        groups = _rows(db, groups_sql, maximum=3, check=check)
        matches = []
        for _, owners in groups:
            check()
            values = _rows(
                db,
                """SELECT node_index,CASE WHEN length(value)>? THEN error('search text node exceeds snippet budget') ELSE value END
                FROM material.html_nodes WHERE content_sha256=? AND node_type='text' AND node_index BETWEEN ? AND ? ORDER BY node_index""",
                [MAX_TEXT, content_id, min(owners), max(owners)],
                maximum=10000,
                check=check,
            )
            text = "".join(value or "" for _, value in values)
            if len(text) > MAX_TEXT:
                raise ValueError("search snippet text exceeds its budget")
            snippet = _snippet(text, query, check)
            match = {"snippet": snippet, "node_indexes": owners}
            if match not in matches:
                matches.append(match)
        output.append({"content_id": content_id, "matches": matches, "score": score})
        if len(json.dumps(output, ensure_ascii=False).encode()) > MAX_BYTES:
            raise ValueError("search result exceeds its collection budget")
        check()
    return output


@dataclass(frozen=True)
class SearchBinding:
    sql: str
    parameters: dict[str, object]


def bind_search(
    sql: str, parameters: list[object], db, *, execute: bool, check: Callable[[], None]
) -> SearchBinding | None:
    # Original syntax/access validation is performed by QueryService before this
    # trusted transformation. Only constant search arguments are accepted.
    numbered = _number_parameters(sql)
    source = numbered if numbered is not None else sql
    explain = _EXPLAIN_PREFIX.match(source.strip())
    tree = _one_statement(source.strip()[explain.end() :] if explain else source)
    calls = [
        t
        for t in tree.find_all(exp.Table)
        if isinstance(t.this, exp.Anonymous) and t.this.name.lower() == "search"
    ]
    if not calls:
        return None
    if isinstance(tree, exp.Describe):
        return None  # The typed catalogue signature supports metadata inspection.
    if explain or not isinstance(tree, exp.Query):
        raise ValueError(
            "Use query preparation to inspect search(); EXPLAIN and SUMMARIZE are not supported for search"
        )
    if len(calls) > 4:
        raise ValueError("at most four search calls are allowed per query")
    bound = {str(i): v for i, v in enumerate(parameters, 1)}
    for placeholder in tree.find_all(exp.Placeholder):
        if str(placeholder.this) not in bound:
            raise ValueError("search queries require positional SQL parameters")
    cache = {}
    for index, table in enumerate(calls):
        args = table.this.expressions
        if len(args) != 1:
            raise ValueError("search expects one query argument")
        arg = args[0]
        if isinstance(arg, exp.Literal) and arg.is_string:
            value = arg.this
        elif isinstance(arg, exp.Null):
            value = None
        elif isinstance(arg, exp.Placeholder) and str(arg.this) in bound:
            value = bound[str(arg.this)]
        else:
            raise ValueError(
                "search requires a string literal or bound parameter, not a row-dependent expression"
            )
        query = parse_query(value)
        key = f"__periplus_search_{index}"
        if query not in cache:
            cache[query] = discover(db, query, check) if execute else []
        bound[key] = cache[query]
        replacement = _one_statement(
            f"SELECT hit.* FROM unnest(${key}::{RESULT_TYPE}) AS hits(hit)"
        ).subquery()
        replacement.set(
            "alias",
            table.args.get("alias") or exp.TableAlias(this=exp.to_identifier("search")),
        )
        table.replace(replacement)
    for placeholder in tree.find_all(exp.Placeholder):
        old = str(placeholder.this)
        if old.isdecimal():
            key = "__periplus_input_" + old
            if old not in bound:
                raise ValueError("missing SQL parameter")
            bound[key] = bound[old]
            placeholder.set("this", key)
    needed = {str(p.this) for p in tree.find_all(exp.Placeholder)}
    return SearchBinding(
        tree.sql(dialect="duckdb"), {k: v for k, v in bound.items() if k in needed}
    )
