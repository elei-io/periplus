"""Disposable retained-source comparison using the shared query benchmark."""

import argparse
from dataclasses import asdict, replace
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
from unittest.mock import patch
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.query.benchmarking import _measure, load_case, deadline


def run(input_dir, count, report):
    parsed = {
        hashlib.sha256(p.read_bytes()).hexdigest(): parse_document(p.read_text())
        for p in sorted(input_dir.glob("*.html"))[:count]
    }
    context = VisitBatchContext(
        (),
        (),
        (),
        {k: v[1] for k, v in parsed.items()},
        {k: v[0] for k, v in parsed.items()},
        {},
        frozenset(parsed),
    )
    with TemporaryDirectory() as directory:
        root = Path(directory)
        config = CatalogueConfig(
            "periplus", str(root / "metadata.duckdb"), str(root / "data"), "ducklake"
        )
        with Catalogue(
            config, duckdb_config={"memory_limit": "512MB", "threads": "2"}
        ) as c:
            c.bootstrap()
            db = c.trusted_connection
            for name in ("html_nodes", "prose"):
                db.register("batch", BY_NAME[name].rows(context))
                db.execute(f"INSERT INTO material.{name} SELECT * FROM batch")
            for key in parsed:
                db.execute(
                    "INSERT INTO ingest.visits (visit_id,document_id,requested_url,effective_url,observed_at,admitted_at,finished_at,outcome) VALUES (uuid(),uuid(),?, ?,now(),now(),now(),'success')",
                    [f"https://example.test/{key}", f"https://example.test/{key}"],
                )
            db.execute(
                "INSERT INTO ingest.documents(document_id,visit_id,content_sha256,detected_media_type,observed_at,representation,content_bytes,object_key,storage_encoding,stored_bytes) SELECT document_id,visit_id,split_part(requested_url,'/',4),'text/html',now(),'response_body',1,'fixture','identity',1 FROM ingest.visits"
            )
            db.execute("USE public_v1")
            cases = Path(__file__).resolve().parents[1] / "cases"
            search = load_case(cases / "search-discovery")
            element = load_case(cases / "element-text")
            # Select a nonempty heading match for independent correctness verification.
            key = next(
                k
                for k, (nodes, els) in parsed.items()
                if any(
                    e.tag == "h1"
                    and "the"
                    in "".join(
                        n.value or ""
                        for n in nodes
                        if n.node_type == "text"
                        and e.element_index < n.node_index < e.subtree_end_index
                    ).lower()
                    for e in els
                )
            )
            element = replace(element, sql=element.sql.replace("'fixture'", f"'{key}'"))
            expected = []
            nodes, els = parsed[key]
            for e in els:
                text = "".join(
                    n.value or ""
                    for n in nodes
                    if n.node_type == "text"
                    and e.element_index < n.node_index < e.subtree_end_index
                )
                if e.tag.lower() == "h1" and "the" in text.lower():
                    expected.append((key, e.element_index, "h1", text))
            with (
                deadline(db, 60),
                patch(
                    "periplus.query.benchmarking.catalogue_config_from_env",
                    return_value=config,
                ),
            ):
                assert db.execute(element.sql).fetchall() == expected
                results = {
                    case.identifier: asdict(_measure(db, case, None, 1))
                    for case in (search, element)
                }
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(
                json.dumps({"documents": len(parsed), "results": results}, indent=2)
            )
            print(
                json.dumps(
                    {
                        k: {
                            x: v[x]
                            for x in (
                                "normal_ms",
                                "median_warm_ms",
                                "result_rows",
                                "total_bytes_read",
                                "scans",
                            )
                        }
                        for k, v in results.items()
                    }
                )
            )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--documents", type=int, default=100)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    run(a.input_dir, a.documents, a.report)
