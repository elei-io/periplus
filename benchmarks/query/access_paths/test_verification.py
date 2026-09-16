import unittest

from verify_results import fingerprint, normalized


class VerificationTests(unittest.TestCase):
    def test_equivalence_accepts_only_unambiguous_column_qualification(self):
        self.assertEqual(normalized([{"e.node_index": 3}]), [{"node_index": 3}])
        with self.assertRaises(ValueError):
            normalized([{"e.node_index": 3, "node_index": 3}])

    def test_answer_comparison_keeps_values_order_and_duplicates(self):
        self.assertEqual(fingerprint([{"e.n": 3}]), fingerprint([{"n": 3}]))
        self.assertNotEqual(fingerprint([{"n": 3}]), fingerprint([{"n": 4}]))
        self.assertNotEqual(fingerprint([{"n": 3}]), fingerprint([{"n": 3}, {"n": 3}]))
        self.assertNotEqual(
            fingerprint([{"n": 3}, {"n": 4}]), fingerprint([{"n": 4}, {"n": 3}])
        )
