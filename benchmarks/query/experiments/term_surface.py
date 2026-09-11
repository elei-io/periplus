"""Exercise proposed public term queries against real local HTML in a disposable lake."""
from dataclasses import replace
import argparse
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from vocabulary_materialization import Experiment, prepare, term_tokens, tokenizer_metadata
from vocabulary_lookup import measure_stage
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.query.benchmarking import deadline, load_case


ROOT = Path(__file__).resolve().parent


def install_surface(c):
    c.execute((ROOT / "term.sql").read_text())
    c.create_function("icu_terms", term_tokens, ["VARCHAR"], "VARCHAR[]")
    # Direct prose segmentation shares ICU semantics, but does not read postings.
    # Explicit multilingual golden tests validate segmentation independently.
    c.execute("""CREATE TEMP VIEW reference_term AS
        SELECT p.content_id, token.text, count(*)::BIGINT AS frequency
        FROM public_v1.prose p,
        unnest(icu_terms(p.text)) token(text)
        GROUP BY p.content_id, token.text
    """)


def add_dom(c, sources, *, create=False, row_group_size=None):
    nodes, elements = {}, {}
    for source in sources:
        key = hashlib.sha256(source.encode()).hexdigest()
        nodes[key], elements[key] = parse_document(source)
    context = VisitBatchContext((), (), (), elements, nodes, {}, frozenset(nodes))
    for name in ("html_nodes",):
        spec = BY_NAME[name]
        c.register("dom_batch", spec.rows(context))
        if create:
            c.execute(f"CREATE TABLE material.{name} AS SELECT * FROM dom_batch LIMIT 0")
            c.execute(f"ALTER TABLE material.{name} SET PARTITIONED BY (bucket(8, content_sha256))")
            c.execute(f"ALTER TABLE material.{name} SET SORTED BY ({', '.join(spec.sort_order)})")
            if row_group_size is not None:
                c.execute("CALL periplus.set_option('parquet_row_group_size', ?, schema=>'material', table_name=>?)",
                          [row_group_size, name])
        c.execute(f"INSERT INTO material.{name} SELECT * FROM dom_batch ORDER BY {', '.join(spec.sort_order)}")
        c.unregister("dom_batch")
    if create:
        resources = files("periplus.platform.catalogue").joinpath("sql/public_v1/views")
        for name in ("html_node", "html_element", "html_heading"):
            c.execute(resources.joinpath(f"{name}.sql").read_text())


def queries(rare, common, second):
    # Terms are selected as ASCII letters only; quote defensively regardless.
    literal = lambda value: "'" + value.replace("'", "''") + "'"
    r, c, s = map(literal, (rare, common, second))
    return {
        "rare": f"SELECT content_id, frequency FROM {{relation}} WHERE text={r} ORDER BY content_id",
        "common": f"SELECT content_id, frequency FROM {{relation}} WHERE text={c} ORDER BY content_id",
        "wildcard": f"SELECT content_id, text, frequency FROM {{relation}} WHERE text ILIKE {literal('%' + rare[:4] + '%')} ORDER BY ALL",
        "all_terms": f"SELECT content_id FROM {{relation}} WHERE text IN ({r},{s}) GROUP BY content_id HAVING count(DISTINCT text)=2 ORDER BY content_id",
        "prose_join": f"SELECT p.content_id, p.text FROM public_v1.prose p WHERE EXISTS (SELECT 1 FROM {{relation}} w WHERE w.content_id=p.content_id AND w.text={r}) ORDER BY p.content_id",
        "heading_join": f"SELECT h.content_id,h.node_index,h.level,h.text FROM public_v1.html_heading h WHERE EXISTS (SELECT 1 FROM {{relation}} w WHERE w.content_id=h.content_id AND w.text={r}) ORDER BY h.content_id,h.node_index",
    }


def run(input_dir, report_path, batch_size=5):
    sources = {}
    for path in sorted(input_dir.rglob("*.html")):
        raw = path.read_bytes()
        # Hash exactly the UTF-8 fixture representation used by the prototype.
        source = raw.decode("utf-8")
        sources[hashlib.sha256(source.encode()).hexdigest()] = source
    if len(sources) < 4:
        raise ValueError("at least four distinct UTF-8 HTML files are required")
    # Deterministic order and no duplicated/scaled copies of the real corpus.
    ordered = [sources[key] for key in sorted(sources)]
    split = len(ordered) // 2
    with TemporaryDirectory(prefix="periplus-term-surface-") as directory:
        root = Path(directory)
        lake = Experiment(root, postings_buckets=0)
        c = lake.connection
        try:
            install_surface(c)
            stages, commits = {}, []
            def ingest(start, end):
                for offset in range(start, end, batch_size):
                    subset = ordered[offset:min(offset + batch_size, end)]
                    started = perf_counter()
                    batch = prepare(root / f"{offset}.parquet", subset)
                    preparation = perf_counter() - started
                    commits.append({"prepare_seconds": preparation, **lake.commit(batch)})
                    add_dom(c, subset, create=offset == 0)
            ingest(0, split)
            # Pick a rare term from a document with headings; ensure nonempty joins.
            term, content_id = c.execute("""
                SELECT w.text, min(w.content_id) FROM public_v1.term w
                WHERE regexp_full_match(w.text, '[a-z]{5,}')
                GROUP BY w.text HAVING count(*)=1
                  AND min(w.content_id) IN (SELECT content_id FROM public_v1.html_heading)
                ORDER BY w.text LIMIT 1
            """).fetchone()
            common = c.execute("SELECT text FROM public_v1.term WHERE regexp_full_match(text,'[a-z]{3,}') GROUP BY text ORDER BY count(*) DESC,text LIMIT 1").fetchone()[0]
            second = c.execute("SELECT text FROM public_v1.term WHERE content_id=? AND text<>? AND regexp_full_match(text,'[a-z]{3,}') ORDER BY frequency DESC,text LIMIT 1", [content_id,term]).fetchone()[0]
            case = load_case(ROOT.parent / "cases/exp-term-discovery")
            with patch.dict(os.environ, {
                "PERIPLUS_DUCKLAKE_ALIAS": "periplus", "PERIPLUS_DUCKLAKE_METADATA_PATH": str(root / "metadata.duckdb"),
                "PERIPLUS_DUCKLAKE_DATA_PATH": str(root / "data"), "PERIPLUS_DUCKLAKE_METADATA_SCHEMA": "ducklake",
            }):
                for name in ("initial", "appended", "compacted"):
                    mutation = None
                    if name == "appended":
                        ingest(split, len(ordered))
                    if name == "compacted":
                        started = perf_counter()
                        with deadline(c, 60):
                            c.execute("CALL ducklake_merge_adjacent_files('periplus','term_stat',schema=>'material')").fetchall()
                        mutation = perf_counter() - started
                    with deadline(c, 60):
                        assert c.execute("SELECT * FROM public_v1.term EXCEPT ALL SELECT * FROM reference_term").fetchall() == []
                        assert c.execute("SELECT * FROM reference_term EXCEPT ALL SELECT * FROM public_v1.term").fetchall() == []
                    measured = {}
                    for query_name, sql in queries(term, common, second).items():
                        measured[query_name] = measure_stage(c, replace(case, identifier=f"term-{query_name}"), {
                            "prose_reference": sql.format(relation="reference_term"),
                            "public_term": sql.format(relation="public_v1.term"),
                        })
                    storage = {}
                    for table in ("vocabulary", "term_stat", "prose"):
                        storage[table] = c.execute("SELECT count(*), coalesce(sum(data_file_size_bytes),0) FROM ducklake_list_files('periplus',?,schema=>'material')", [table]).fetchone()
                    stages[name] = {"counts": lake.validate(), "storage_files_bytes": storage,
                                    "merge_seconds": mutation, "queries": measured}
            report = {"complete": True, "source_count": len(ordered), "source_bytes": sum(len(s.encode()) for s in ordered),
                      "tokenizer": tokenizer_metadata(),
                      "corpus_digest": hashlib.sha256(''.join(sorted(sources)).encode()).hexdigest(),
                      "batch_size": batch_size, "terms": {"rare":term,"common":common,"second":second},
                      "commits": commits, "stages": stages}
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps({"complete": True, "contents": len(ordered),
                              "stages": {key: value["counts"] for key,value in stages.items()}}, indent=2))
        finally:
            lake.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=5)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch size must be positive")
    run(args.input_dir, args.report, args.batch_size)
