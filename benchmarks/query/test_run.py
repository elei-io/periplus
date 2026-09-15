import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import run


class RunnerTests(unittest.TestCase):
    def test_scope_binding_does_not_count_question_marks_in_regex_literals(self):
        sql, parameters = run.bind_scope("SELECT '^https?://' WHERE n <= $scope", 50)
        self.assertEqual(sql, "SELECT '^https?://' WHERE n <= ?")
        self.assertEqual(parameters, [50])

    def test_result_records_digest_and_truncation_without_raw_rows(self):
        payload = {"rows": [["private", 1], ["private", 1]], "truncated": True, "query_id": "q", "columns": ["text", "n"], "types": ["String", "Int32"]}
        process = SimpleNamespace(returncode=0, stdout=json.dumps(payload)+"\n200")
        with patch.object(run.subprocess, "run", return_value=process):
            result = run.execute("https://example.test/api/query", "SELECT 1", [], "exec")
        self.assertEqual(result["status"], "truncated")
        self.assertEqual(result["returned_rows"], 2)
        self.assertNotIn("rows", result)
        self.assertNotIn("private", json.dumps(result))

    def test_gateway_html_is_not_recorded_as_a_query_result(self):
        with patch.object(run.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="<html>gateway</html>\n502")):
            result = run.execute("https://example.test/api/query", "SELECT 1", [], "exec")
        self.assertEqual(result["status"], "gateway_failure")
        self.assertEqual(result["http_status"], 502)
