"""Disposable posting-layout comparison using the shared measurement runner.

Positions are document-global ICU token ordinals with contributing node IDs.
The variants isolate discovery and frequency aggregation, not phrase execution.
"""

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import duckdb
import icu
import pyarrow as pa
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.search_text import body_parts, build_search_text
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.query.benchmarking import QueryCase, _measure


def positional_tokens(nodes):
    """Research occurrence projection; verified against both production counters."""
    chars, owners = [], []
    pending = False
    for text, owner in body_parts(nodes):
        for char in text:
            if char.isspace():
                pending = bool(chars)
                continue
            if pending:
                chars.append(" ")
                owners.append(None)
                pending = False
            chars.append(char)
            owners.append(owner)
    normalizer = icu.Normalizer2.getNFCInstance()
    parts, mapped, segment, segment_owners = [], [], [], set()

    def flush():
        if segment:
            normalized = normalizer.normalize("".join(segment))
            parts.append(normalized)
            mapped.extend(
                [frozenset(segment_owners)] * (len(normalized.encode("utf-16-le")) // 2)
            )
            segment.clear()
            segment_owners.clear()

    for char, owner in zip(chars, owners, strict=True):
        folded = icu.UnicodeString(char)
        folded.foldCase()
        for ch in str(folded):
            if normalizer.hasBoundaryBefore(ch):
                flush()
            segment.append(ch)
            if owner is not None:
                segment_owners.add(owner)
    flush()
    normalized = icu.UnicodeString("".join(parts))
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(normalized)
    start, result = iterator.first(), []
    for end in iterator:
        if iterator.getRuleStatus() >= 100:
            result.append(
                (str(normalized[start:end]), sorted(set().union(*mapped[start:end])))
            )
        start = end
    return result


def measure(db, name, sql):
    case = QueryCase(
        "posting-summary",
        name,
        "Posting layout",
        "schema",
        True,
        (None,),
        "4GiB",
        60000,
        sql,
        Path("/tmp"),
        seconds=120,
    )
    return asdict(_measure(db, case, None, 1))


def query(relation, layout, ids, ranked):
    predicate = ",".join(map(str, ids))
    if layout == "packed_node":
        counts = f"SELECT content_id,term_id,count(DISTINCT position)::BIGINT frequency FROM (SELECT content_id,term_id,unnest(positions) AS position FROM {relation} WHERE term_id IN ({predicate})) GROUP BY content_id,term_id"
    elif layout == "flat":
        counts = f"SELECT content_id,term_id,count(*)::BIGINT frequency FROM {relation} WHERE term_id IN ({predicate}) GROUP BY content_id,term_id"
    elif layout == "packed":
        counts = f"SELECT content_id,term_id,len(positions)::BIGINT frequency FROM {relation} WHERE term_id IN ({predicate})"
    else:
        counts = f"SELECT content_id,term_id,frequency FROM {relation} WHERE term_id IN ({predicate})"
    if not ranked:
        counts = f"SELECT DISTINCT content_id,term_id FROM {relation} WHERE term_id IN ({predicate})"
    hits = (
        "SELECT content_id"
        + (",sum(frequency)::BIGINT score" if ranked else "")
        + f" FROM ({counts}) GROUP BY content_id HAVING count(*)={len(ids)}"
    )
    # Full-set fingerprint plus counts avoids the bench output cap on common terms.
    fields = "content_id,score" if ranked else "content_id"
    return (
        f"WITH hits AS ({hits}) SELECT count(*)::BIGINT contents,bit_xor(hash({fields})) digest"
        + (",sum(score)::HUGEINT total_frequency" if ranked else "")
        + " FROM hits"
    )


def compare(db, root, label, files, workloads, emit):
    layouts = ["summary", "flat", "packed_node", "packed", "packed_frequency"]
    for layout in layouts:
        # packed_frequency uses exactly the packed file, projecting its scalar frequency.
        source = "packed" if layout == "packed_frequency" else layout
        paths = (
            "["
            + ",".join("'" + str(p).replace("'", "''") + "'" for p in files[source])
            + "]"
        )
        db.execute(
            f"CREATE OR REPLACE TEMP VIEW {layout} AS SELECT * FROM read_parquet({paths})"
        )
    for words, ids in workloads:
        for ranked in [False, True]:
            reference = None
            for order in [layouts, list(reversed(layouts))]:
                for layout in order:
                    r = measure(db, label, query(layout, layout, ids, ranked))
                    signature = (r["result_digest"], tuple(r["types"]))
                    if reference is None:
                        reference = signature
                    assert reference == signature, (label, words, ranked, layout)
                    emit(
                        {
                            "kind": "measurement",
                            "label": label,
                            "words": words,
                            "ranked": ranked,
                            "layout": layout,
                            "result": r,
                        }
                    )


def write_batch(db, root, batch, source):
    result = {}
    statements = {
        "flat": f"SELECT term_id,content_id,position,node_indexes FROM ({source}) ORDER BY term_id,content_id,position",
        "summary": f"SELECT term_id,content_id,count(*)::BIGINT frequency FROM ({source}) GROUP BY term_id,content_id ORDER BY term_id,content_id",
        "packed_node": f"SELECT term_id,content_id,node_index,list(position ORDER BY position) positions FROM (SELECT term_id,content_id,position,unnest(node_indexes) node_index FROM ({source})) GROUP BY term_id,content_id,node_index ORDER BY term_id,content_id,node_index",
        "packed": f"SELECT term_id,content_id,count(*)::BIGINT frequency,list(position ORDER BY position) positions,list(node_indexes ORDER BY position) node_indexes FROM ({source}) GROUP BY term_id,content_id ORDER BY term_id,content_id",
    }
    for layout, sql in statements.items():
        path = root / f"{batch}-{layout}.parquet"
        db.execute(
            f"COPY ({sql}) TO '{path}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 32768)"
        )
        result[layout] = path
    return result


def run(input_dir, documents, output, growth_documents=100000, rare_id=5003):
    def emit(value):
        output.write(json.dumps(value) + "\n")
        output.flush()
        if value["kind"] != "measurement":
            print(json.dumps(value), flush=True)

    with TemporaryDirectory(prefix="periplus-posting-summary-") as temporary:
        root = Path(temporary)
        db = duckdb.connect()
        db.execute("SET threads=2; SET memory_limit='4GiB'; LOAD ducklake")
        db.execute(
            f"ATTACH 'ducklake:{root}/metadata.duckdb' AS periplus (DATA_PATH '{root}/lake')"
        )
        config = CatalogueConfig(
            "periplus", str(root / "metadata.duckdb"), str(root / "lake"), "ducklake"
        )
        with patch(
            "periplus.query.benchmarking.catalogue_config_from_env", return_value=config
        ):
            started = time.perf_counter()
            paths = sorted(input_dir.glob("*.html"))[:documents]
            if len(paths) != documents:
                raise ValueError("insufficient retained HTML")
            dictionary, contents, terms, positions, owners = {}, [], [], [], []
            for i, path in enumerate(paths):
                nodes, _ = parse_document(path.read_text())
                text = build_search_text(nodes)
                tokens = positional_tokens(nodes)
                assert Counter(t for t, _ in tokens) == text.content_counts
                assert (
                    Counter((t, n) for t, ns in tokens for n in ns) == text.node_counts
                )
                for position, (token, node_indexes) in enumerate(tokens):
                    term_id = dictionary.setdefault(token, len(dictionary) + 1)
                    contents.append(path.stem)
                    terms.append(term_id)
                    positions.append(position)
                    owners.append(node_indexes)
            db.register(
                "real_tokens",
                pa.table(
                    {
                        "content_id": pa.array(contents, pa.string()),
                        "term_id": pa.array(terms, pa.int64()),
                        "position": pa.array(positions, pa.int32()),
                        "node_indexes": pa.array(owners, pa.list_(pa.int32())),
                    }
                ),
            )
            files = write_batch(db, root, "real", "SELECT * FROM real_tokens")
            emit(
                {
                    "kind": "corpus",
                    "label": "real",
                    "documents": documents,
                    "tokens": len(terms),
                    "vocabulary": len(dictionary),
                    "preparation_seconds": time.perf_counter() - started,
                    "bytes": {k: p.stat().st_size for k, p in files.items()},
                }
            )
            workloads = [
                (word, [dictionary[word]])
                for word in ["the", "robot", "microcontroller"]
                if word in dictionary
            ]
            if "robot" in dictionary and "the" in dictionary:
                workloads.append(
                    ("robot AND the", [dictionary["robot"], dictionary["the"]])
                )
            compare(
                db, root, "real", {k: [v] for k, v in files.items()}, workloads, emit
            )
            db.unregister("real_tokens")
            del contents, terms, positions, owners
            # 200 tokens/document. Rare term 5003 occurs in exactly ten fixed documents.
            # Common term 1 and pair 1+2 grow with the corpus. IDs never change.
            files = {k: [] for k in ["summary", "flat", "packed_node", "packed"]}
            for batch in range(growth_documents // 10000):
                offset = batch * 10000
                source = f"""SELECT sha256((d+{offset})::VARCHAR) content_id,p::INTEGER AS position,[(p//10)::INTEGER] node_indexes,
                  CASE WHEN d+{offset}<10 AND p=0 THEN {rare_id}
                       WHEN p%10=0 THEN 1 WHEN p%10=1 THEN 2
                       ELSE 3+hash(d+{offset},p)%10000 + CASE WHEN hash(d+{offset},p)%10000 >= {rare_id - 3} THEN 1 ELSE 0 END END::BIGINT term_id
                  FROM range(10000) ds(d) CROSS JOIN range(200) ps(p)"""
                added = write_batch(db, root, f"growth-{batch}", source)
                for k, p in added.items():
                    files[k].append(p)
                if batch in {0, 2, 9, growth_documents // 10000 - 1}:
                    label = f"append-{(batch + 1) * 10000}"
                    emit(
                        {
                            "kind": "corpus",
                            "label": label,
                            "documents": (batch + 1) * 10000,
                            "tokens": (batch + 1) * 2000000,
                            "files_per_layout": batch + 1,
                            "rare_id": rare_id,
                            "bytes": {
                                k: sum(p.stat().st_size for p in ps)
                                for k, ps in files.items()
                            },
                        }
                    )
                    compare(
                        db,
                        root,
                        label,
                        files,
                        [
                            ("fixed rare", [rare_id]),
                            ("common", [1]),
                            ("common AND", [1, 2]),
                        ],
                        emit,
                    )
            # Same final corpus, globally sorted and compacted into one file per layout.
            compact = {}
            for layout in files:
                source = (
                    "SELECT * FROM read_parquet(["
                    + ",".join("'" + str(p) + "'" for p in files[layout])
                    + "]) ORDER BY term_id,content_id"
                )
                p = root / f"compact-{layout}.parquet"
                db.execute(
                    f"COPY ({source}) TO '{p}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 32768)"
                )
                compact[layout] = [p]
            emit(
                {
                    "kind": "corpus",
                    "label": f"compact-{growth_documents}",
                    "documents": growth_documents,
                    "tokens": growth_documents * 200,
                    "files_per_layout": 1,
                    "bytes": {k: ps[0].stat().st_size for k, ps in compact.items()},
                }
            )
            compare(
                db,
                root,
                f"compact-{growth_documents}",
                compact,
                [("fixed rare", [rare_id]), ("common", [1]), ("common AND", [1, 2])],
                emit,
            )
            emit({"kind": "complete", "complete": True})
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--documents", type=int, default=500)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--growth-documents", type=int, default=100000)
    parser.add_argument("--rare-id", type=int, choices=[5003, 9999999], default=5003)
    args = parser.parse_args()
    if not 1 <= args.documents <= 1000:
        parser.error("documents must be 1..1000")
    if not 10000 <= args.growth_documents <= 1000000 or args.growth_documents % 10000:
        parser.error("growth-documents must be a multiple of 10000, up to 1000000")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w") as output:
        run(args.input_dir, args.documents, output, args.growth_documents, args.rare_id)
