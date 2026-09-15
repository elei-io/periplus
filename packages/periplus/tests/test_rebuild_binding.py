"""Publication binding preserves relation/column aliases and public validation."""
import unittest
from periplus.query.binding import PublicationBinding, bind_publication
from periplus.query.models import QueryRequest
from periplus.query.service import public_sql


class BindingTests(unittest.TestCase):
    def test_all_relations_use_one_binding(self):
        binding = PublicationBinding(expires_at="2099-01-01T00:00:00Z", database='query_' + 'a' * 32, revision=2)
        queries = [
            'SELECT c.capture_id,l.target_url FROM capture c JOIN link l USING(capture_id)',
            'SELECT capture.capture_id FROM capture',
            'SELECT public_v1.capture.capture_id FROM public_v1.capture',
            'WITH capture AS (SELECT capture_id FROM public_v1.capture) SELECT * FROM capture',
            'SELECT capture_id FROM capture UNION ALL SELECT capture_id FROM link',
            'SELECT capture_id FROM capture UNION ALL (WITH capture AS (SELECT capture_id FROM public_v1.capture) SELECT * FROM capture)',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                result = bind_publication(public_sql(QueryRequest(sql=sql)), binding)
                self.assertNotIn('public_v1.', result)
                self.assertIn(binding.database, result)
        with self.assertRaises(ValueError):
            public_sql(QueryRequest(sql=f'SELECT * FROM {binding.database}.capture'))

    def test_cte_in_other_union_branch_does_not_hide_public_table(self):
        sql = public_sql(QueryRequest(sql='SELECT capture_id FROM capture UNION ALL (WITH capture AS (SELECT capture_id FROM public_v1.capture) SELECT * FROM capture)'))
        self.assertEqual(sql.count('public_v1.capture'), 2)
