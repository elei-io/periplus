import unittest

from materialization.executor import (
    _jsonld_type_terms,
    _relation_scope,
    workloads,
)


class FixedMaterializationTests(unittest.TestCase):
    def test_topology_is_fixed_and_has_one_source_per_target(self) -> None:
        self.assertEqual(
            [
                (
                    item.source_schema,
                    item.source_table,
                    item.target_table,
                    item.durable,
                )
                for item in workloads()
            ],
            [
                (
                    "ingest",
                    "documents",
                    "html_elements",
                    "atlas-material-html_elements-v1",
                ),
                (
                    "material",
                    "html_elements",
                    "jsonld_values",
                    "atlas-material-jsonld_values-v1",
                ),
                (
                    "ingest",
                    "visits",
                    "pages",
                    "atlas-material-pages-v1",
                ),
                (
                    "ingest",
                    "documents",
                    "links",
                    "atlas-material-links-v1",
                ),
            ],
        )

    def test_jsonld_type_terms_are_distinct_raw_strings(self) -> None:
        value = {
            "@type": ["Article", "Thing", 42],
            "@graph": [{"@type": "Article"}, {"nested": {"@type": "Person"}}],
        }
        self.assertEqual(
            _jsonld_type_terms(value), {"Article", "Thing", "Person"}
        )

    def test_relation_scope_precedence(self) -> None:
        source = "https://www.example.com/a"
        self.assertEqual(_relation_scope(source, source), "self")
        self.assertEqual(
            _relation_scope(source, "https://www.example.com/b"),
            "same_origin",
        )
        self.assertEqual(
            _relation_scope(source, "http://www.example.com/b"), "same_host"
        )
        self.assertEqual(
            _relation_scope(source, "https://api.example.com/b"), "same_site"
        )
        self.assertEqual(
            _relation_scope(source, "https://example.net/b"), "external"
        )


if __name__ == "__main__":
    unittest.main()
