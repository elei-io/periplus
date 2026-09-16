"""The explorer and autocomplete must receive each public view's column names."""
import unittest

from periplus.query.helpers import query_helpers


class QueryHelperTests(unittest.TestCase):
    def test_public_columns_match_the_catalogue_contract(self):
        helpers = query_helpers()
        actual = {
            relation.name: [column.name for column in relation.columns]
            for relation in helpers.relations
        }
        self.assertEqual(actual, {
            "public_v1.capture": ["capture_id", "url", "captured_at",
                "http_status_code", "document_id", "byte_length", "encoding", "text", "element_count"],
            "public_v1.html_element": ["document_id", "node_index", "parent_index",
                "subtree_end_index", "sibling_index", "depth", "tag", "namespace", "attributes",
                "text_direct", "text"],
            "public_v1.link": ["capture_id", "node_index", "target_url", "raw_href"],
            "public_v1.page": ["url"],
            "public_v1.html_metadata": ["document_id", "node_index", "name", "property", "http_equiv", "charset", "content"],
            "public_v1.html_jsonld": ["document_id", "node_index", "json", "types", "name"],
        })
        self.assertEqual(helpers.schema_version, "public_v1")

    def test_unknown_schema_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported public schema"):
            query_helpers("unknown")
