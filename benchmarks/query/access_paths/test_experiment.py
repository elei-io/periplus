import unittest

from client import literal, target
from experiment import digest
from load import element_insert


class BoundaryTests(unittest.TestCase):
    def test_destinations_cannot_escape_benchmark_database(self):
        for name in ["material.captures", "x; DROP DATABASE material", "../source", ""]:
            with self.assertRaises(ValueError):
                target(name)

    def test_literals_quote_both_escape_and_apostrophe(self):
        self.assertEqual(literal("a\\b'c"), "'a\\\\b\\'c'")

    def test_result_identity_keeps_duplicates_and_order(self):
        self.assertNotEqual(
            digest({"response": {"data": [{"x": 1}, {"x": 1}]}}),
            digest({"response": {"data": [{"x": 1}]}}),
        )
        self.assertNotEqual(
            digest({"response": {"data": [{"x": 1}, {"x": 2}]}}),
            digest({"response": {"data": [{"x": 2}, {"x": 1}]}}),
        )

    def test_complete_text_has_no_silent_truncation(self):
        sql = element_insert("elements_full", 0, 200, True)
        self.assertIn("e.text_end-e.text_start", sql)
        self.assertNotIn("least(", sql.lower())
