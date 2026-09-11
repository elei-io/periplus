import importlib.util
from pathlib import Path
import unittest

import duckdb

from periplus.materialization.tokenization import term_counts

spec = importlib.util.spec_from_file_location(
    "vocabulary_search_experiment",
    Path(__file__).resolve().parents[3]
    / "benchmarks/query/experiments/vocabulary_search.py",
)
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class VocabularySearchExperimentTests(unittest.TestCase):
    def test_candidates_preserve_substrings_and_phrase_order_is_verified(self):
        with duckdb.connect() as db:
            db.execute("CREATE SCHEMA material; CREATE SCHEMA public_v1")
            db.execute(
                "CREATE TABLE material.prose(content_sha256 VARCHAR,text VARCHAR)"
            )
            db.execute("CREATE TABLE material.term(text VARCHAR,term_id BIGINT)")
            db.execute(
                "CREATE TABLE material.content_posting(term_id BIGINT,content_sha256 VARCHAR,frequency BIGINT)"
            )
            db.execute("CREATE TABLE public_v1.capture(content_id VARCHAR)")
            bodies = [
                "robot",
                "robotics",
                "wild robot",
                "robotic",
                "wild robotics",
                "robot wild",
                "unrelated",
                "robot-less",
                "ROBOT",
                "微型robot日本",
                "rock\u0027n robot",
            ]
            terms = {}
            for i, body in enumerate(bodies):
                key = str(i)
                db.execute("INSERT INTO material.prose VALUES (?,?)", [key, body])
                db.execute("INSERT INTO public_v1.capture VALUES (?)", [key])
                for term, count in term_counts(body).items():
                    if term not in terms:
                        terms[term] = len(terms) + 1
                        db.execute(
                            "INSERT INTO material.term VALUES (?,?)",
                            [term, terms[term]],
                        )
                    db.execute(
                        "INSERT INTO material.content_posting VALUES (?,?,?)",
                        [terms[term], key, count],
                    )
            db.execute("INSERT INTO public_v1.capture VALUES ('0')")
            for query, anchor in [
                ("robot", "robot"),
                ("wild robot", "robot"),
                ("robot wild", "robot"),
                ("rock\u0027n robot", "robot"),
                ("astronaut", "astronaut"),
            ]:
                baseline, _, candidate = experiment.queries(query, anchor)
                self.assertEqual(
                    db.execute(candidate).fetchall(), db.execute(baseline).fetchall()
                )
            baseline, _, _ = experiment.queries("robot", "robot")
            matches = db.execute(baseline).fetchall()
            exact = db.execute(
                "SELECT content_sha256 FROM material.content_posting JOIN material.term USING(term_id) WHERE text='robot'"
            ).fetchall()
            self.assertIn(("1",), matches)
            self.assertNotIn(("1",), exact)
            from dataclasses import asdict
            from html import escape
            import sys
            from unittest.mock import patch
            import pyarrow as pa
            from periplus.materialization.dom.nodes import parse_document
            from test_search_matches_experiment import experiment as mapper

            node_rows = [
                dict(content_sha256=str(i), **asdict(n))
                for i, body in enumerate(bodies)
                for n in parse_document("<p>" + escape(body) + "</p>")[0]
            ]
            db.register("node_rows", pa.Table.from_pylist(node_rows))
            db.execute("CREATE TABLE material.html_nodes AS SELECT * FROM node_rows")
            with patch.dict(sys.modules, {"search_matches": mapper}):
                rows, stats = experiment.indexed_matches(
                    db, "wild robot", "robot", limit=1
                )
            self.assertEqual([r["content_id"] for r in rows], ["2"])
            self.assertGreater(stats["checked_contents"], 1)
            self.assertEqual(set(rows[0]), {"content_id", "matches", "score"})

    def test_rejects_unsupported_anchor_shapes(self):
        for query, anchor in [
            ("robot", "ro"),
            ("robot", "%"),
            ("日本語", "日本語"),
            ("robot", "monkey"),
        ]:
            with self.assertRaises(ValueError):
                experiment.queries(query, anchor)
