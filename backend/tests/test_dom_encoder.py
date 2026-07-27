from __future__ import annotations

from dataclasses import replace
import unittest
from xml.etree.ElementTree import Element, SubElement

from dom import (
    ElementRow,
    encode_html,
    iter_tree_elements,
    links_from_elements,
    links_from_html,
)

HTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"


class DomEncoderTests(unittest.TestCase):
    def test_deep_tree_projection_does_not_depend_on_python_recursion(self) -> None:
        root = Element("root")
        leaf = root
        for _ in range(1_500):
            leaf = SubElement(leaf, "nested")

        rows = list(iter_tree_elements(root))

        self.assertEqual(len(rows), 1_501)
        self.assertEqual(rows[0].subtree_end_index, 1_501)
        self.assertEqual(rows[-1].depth, 1_500)

    def test_emits_depth_first_document_order_and_parent_indexes(self) -> None:
        rows = encode_html(
            "<!doctype html><html><head><title>T</title></head>"
            "<body><main><p>One</p><p>Two <b>bold</b>.</p></main></body></html>"
        )

        self.assertEqual(
            [(row.element_index, row.parent_index, row.tag) for row in rows],
            [
                (0, None, "html"),
                (1, 0, "head"),
                (2, 1, "title"),
                (3, 0, "body"),
                (4, 3, "main"),
                (5, 4, "p"),
                (6, 4, "p"),
                (7, 6, "b"),
            ],
        )
        self.assertEqual(rows[4].subtree_end_index, 8)
        self.assertEqual(rows[5].child_index, 0)
        self.assertEqual(rows[6].child_index, 1)
        self.assertEqual(rows[7].depth, 4)
        self.assertEqual(rows[6].text_direct, "Two ")
        self.assertEqual(rows[7].text_direct, "bold")
        self.assertEqual(rows[7].text_tail, ".")

    def test_html5_parser_repairs_fragments_deterministically(self) -> None:
        first = encode_html("<p>first<p>second")
        second = encode_html("<p>first<p>second")

        self.assertEqual(first, second)
        self.assertEqual([row.tag for row in first], ["html", "head", "body", "p", "p"])
        self.assertEqual(
            [row.text_direct for row in first[-2:]], ["first", "second"]
        )

    def test_preserves_empty_and_absent_attribute_values(self) -> None:
        row = next(
            row
            for row in encode_html('<input disabled value="" data-value="a&amp;b">')
            if row.tag == "input"
        )

        self.assertEqual(
            row.attributes,
            {"data-value": "a&b", "disabled": "", "value": ""},
        )
        self.assertNotIn("missing", row.attributes)

    def test_records_html_svg_and_namespaced_attribute_namespaces(self) -> None:
        rows = encode_html(
            '<svg><use xlink:href="#icon" xmlns:xlink="http://www.w3.org/1999/xlink">'
            "</use></svg>"
        )
        html_row = rows[0]
        svg_row = next(row for row in rows if row.tag == "svg")
        use_row = next(row for row in rows if row.tag == "use")

        self.assertEqual(html_row.namespace_uri, HTML_NAMESPACE)
        self.assertEqual(svg_row.namespace_uri, SVG_NAMESPACE)
        self.assertEqual(use_row.namespace_uri, SVG_NAMESPACE)
        self.assertEqual(use_row.attributes[f"{{{XLINK_NAMESPACE}}}href"], "#icon")

    def test_folds_text_around_omitted_comments_without_normalizing_it(self) -> None:
        rows = encode_html(
            "<div>before<!-- first -->between<span>inside</span>"
            "after<!-- second -->last</div>"
        )
        div = next(row for row in rows if row.tag == "div")
        span = next(row for row in rows if row.tag == "span")

        self.assertEqual(div.text_direct, "beforebetween")
        self.assertEqual(span.text_direct, "inside")
        self.assertEqual(span.text_tail, "afterlast")

    def test_does_not_collapse_whitespace_or_resolve_urls(self) -> None:
        anchor = next(
            row
            for row in encode_html('<a href="../docs">  A\n  B  </a>')
            if row.tag == "a"
        )

        self.assertEqual(anchor.attributes["href"], "../docs")
        self.assertEqual(anchor.text_direct, "  A\n  B  ")

    def test_rows_are_page_local_and_have_exact_v1_shape(self) -> None:
        row = encode_html("")[0]

        self.assertIsInstance(row, ElementRow)
        self.assertEqual(
            tuple(row.__dataclass_fields__),
            (
                "element_index",
                "parent_index",
                "subtree_end_index",
                "depth",
                "child_index",
                "tag",
                "namespace_uri",
                "attributes",
                "text_direct",
                "text_tail",
            ),
        )

    def test_rejects_non_string_source(self) -> None:
        with self.assertRaisesRegex(TypeError, "source must be a string"):
            encode_html(b"<p>bytes</p>")  # type: ignore[arg-type]

    def test_link_projection_preserves_subtree_order_and_honors_base_href(self) -> None:
        html = (
            '<html><head><base href="/assets/"></head><body>'
            '<a href="guide?utm_source=mail&b=2&a=1#section" title=" Read ">'
            "<span>Nested <b>bold</b> after</span> tail</a>"
            '<a href="//outside.example/path"> External </a>'
            '<a href="guide?a=1&b=2">duplicate</a>'
            '<a href=" ">ignored</a>'
            "</body></html>"
        )

        projected = links_from_html(
            html,
            page_url="https://www.example.com/dir/page",
        )

        self.assertEqual(
            projected,
            {
                "internal": [
                    _link_payload(
                        raw_href="guide?utm_source=mail&b=2&a=1#section",
                        target_url=(
                            "https://www.example.com/assets/guide"
                            "?utm_source=mail&b=2&a=1"
                        ),
                        target_fragment="section",
                        element_index=4,
                        relation_kind="same_origin",
                    ),
                    _link_payload(
                        raw_href="guide?a=1&b=2",
                        target_url="https://www.example.com/assets/guide?a=1&b=2",
                        element_index=8,
                        relation_kind="same_origin",
                    )
                ],
                "external": [
                    _link_payload(
                        raw_href="//outside.example/path",
                        target_url="https://outside.example/path",
                        element_index=7,
                        relation_kind="external",
                    )
                ],
            },
        )
        self.assertEqual(
            projected,
            links_from_elements(
                encode_html(html),
                page_url="https://www.example.com/dir/page",
            ),
        )

    def test_link_projection_ignores_null_href_evidence(self) -> None:
        rows = encode_html("<a>ignored</a>")
        anchor_index = next(
            index for index, row in enumerate(rows) if row.tag == "a"
        )
        rows[anchor_index] = replace(
            rows[anchor_index],
            attributes={"href": None},  # type: ignore[dict-item]
        )

        self.assertEqual(
            links_from_elements(rows, page_url="https://example.com/"),
            {"internal": [], "external": []},
        )


def _link_payload(
    *,
    raw_href: str,
    target_url: str,
    element_index: int,
    relation_kind: str,
    target_fragment: str | None = None,
) -> dict[str, object]:
    from urllib.parse import urlsplit

    target_parts = urlsplit(target_url)
    return {
        "raw_href": raw_href,
        "source_url": "https://www.example.com/dir/page",
        "source_scheme": "https",
        "source_host": "www.example.com",
        "source_port": 443,
        "source_registrable_domain": "example.com",
        "source_path": "/dir/page",
        "source_query": None,
        "target_url": target_url,
        "target_scheme": target_parts.scheme,
        "target_host": target_parts.hostname,
        "target_port": 443,
        "target_path": target_parts.path,
        "target_query": target_parts.query or None,
        "target_fragment": target_fragment,
        "relation_kind": relation_kind,
        "element_index": element_index,
    }


if __name__ == "__main__":
    unittest.main()
