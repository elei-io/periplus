"""Completeness and identity contract for the pinned native DOM adapter."""

import unittest

from periplus.materialization.dom.links import links_from_elements
from periplus.materialization.dom.nodes import parse_document


class LexborContractTests(unittest.TestCase):
    def test_links_inside_template_fragments_preserve_node_references(self):
        nodes, elements = parse_document(
            '<template><a href="/inside">inside</a></template><a href="/outside">outside</a>'
        )
        links = links_from_elements(elements, page_url="https://example.com/")[
            "internal"
        ]
        self.assertEqual(len(links), 2)
        for link in links:
            self.assertEqual(nodes[link["element_index"]].name, "a")

    def test_nested_templates_preserve_fragments_text_comments_and_attributes(self):
        source = (
            '<template id="outer">before<!-- exact  -->'
            '<template data-x="a&amp;b"><b>inside</b></template>after</template>'
        )
        nodes, elements = parse_document(source)
        self.assertEqual(parse_document(source), (nodes, elements))
        self.assertEqual(
            [n.value for n in nodes if n.node_type == "text"],
            ["before", "inside", "after"],
        )
        self.assertEqual(
            [n.value for n in nodes if n.node_type == "comment"], [" exact  "]
        )
        fragments = [n for n in nodes if n.node_type == "document_fragment"]
        self.assertEqual(len(fragments), 2)
        for fragment in fragments:
            self.assertEqual(nodes[fragment.parent_index].name, "template")
        templates = [e for e in elements if e.tag == "template"]
        self.assertEqual(templates[1].attributes, {"data-x": "a&b"})
        self.assertEqual([e.text_direct for e in templates], ["", ""])

    def test_foreign_names_and_distinct_attribute_values_survive(self):
        _, elements = parse_document(
            '<svg viewBox="0 0 1 1"><use href="plain" xlink:href="qualified" '
            'lang="en" xml:lang="fi"/></svg><math><mi>α</mi></math>'
        )
        svg = next(e for e in elements if e.tag == "svg")
        self.assertEqual(svg.namespace_uri, "http://www.w3.org/2000/svg")
        self.assertEqual(svg.attributes, {"viewBox": "0 0 1 1"})
        use = next(e for e in elements if e.tag == "use")
        self.assertEqual(
            use.attributes,
            {
                "href": "plain",
                "{http://www.w3.org/1999/xlink}href": "qualified",
                "lang": "en",
                "{http://www.w3.org/XML/1998/namespace}lang": "fi",
            },
        )
        mi = next(e for e in elements if e.tag == "mi")
        self.assertEqual(mi.namespace_uri, "http://www.w3.org/1998/Math/MathML")
        self.assertEqual(mi.text_direct, "α")

    def test_all_text_locations_and_raw_text_are_preserved(self):
        nodes, _ = parse_document(
            "<title>A&amp;B</title><style>x > y {}</style>"
            '<script>if (a < b) x="&amp;";</script>'
            "<body><p>東京\r\n中文 &amp; Straße</p></body>"
        )
        self.assertEqual(
            [n.value for n in nodes if n.node_type == "text"],
            ["A&B", "x > y {}", 'if (a < b) x="&amp;";', "東京\n中文 & Straße"],
        )

    def test_bom_meta_and_default_decoding(self):
        cases = [
            (b'<meta charset="windows-1252"><p>caf\xe9', "café"),
            (b"<p>caf\xe9", "café"),
            ("\ufeff<p>東京".encode(), "東京"),
            ("<p>東京".encode("utf-16"), "東京"),
            (b'<meta charset="utf-8"><p>bad\xff', "bad�"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                nodes, _ = parse_document(source)
                self.assertEqual(
                    "".join(n.value for n in nodes if n.node_type == "text"), expected
                )

    def test_complete_preorder_has_consistent_parent_sibling_and_subtree_ranges(self):
        fixtures = [
            "",
            "<!doctype html><?hello x><!-- hi -->",
            "<table>outside<tr><td>A<td>B</table><p>a<b>b</p>c",
            "<template><template>x</template>y</template>",
            "<div>" * 1500 + "deep" + "</div>" * 1500,
        ]
        for source in fixtures:
            with self.subTest(source=source[:80]):
                nodes, elements = parse_document(source)
                children = {}
                for index, node in enumerate(nodes):
                    self.assertEqual(node.node_index, index)
                    self.assertGreater(node.subtree_end_index, index)
                    self.assertLessEqual(node.subtree_end_index, len(nodes))
                    if node.parent_index is None:
                        self.assertEqual(index, 0)
                    else:
                        parent = nodes[node.parent_index]
                        self.assertLess(node.parent_index, index)
                        self.assertLess(index, parent.subtree_end_index)
                        self.assertLessEqual(
                            node.subtree_end_index, parent.subtree_end_index
                        )
                        self.assertEqual(node.depth, parent.depth + 1)
                        self.assertEqual(
                            node.sibling_index, children.get(node.parent_index, 0)
                        )
                        children[node.parent_index] = node.sibling_index + 1
                self.assertEqual(
                    [e.element_index for e in elements],
                    [n.node_index for n in nodes if n.node_type == "element"],
                )
