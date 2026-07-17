from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement

from dom import (
    DOM_SCHEMA_VERSION,
    ELEMENT_COLUMNS,
    ElementRow,
    encode_html,
    iter_tree_elements,
    links_from_elements,
    links_from_html,
    write_dom_parquet,
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
        self.assertEqual(rows[0].subtree_end_index, 1_500)
        self.assertEqual(rows[-1].depth, 1_500)

    def test_parquet_element_budget_removes_partial_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.parquet"
            with self.assertRaisesRegex(ValueError, "element budget"):
                write_dom_parquet(
                    "<html><body><div></div></body></html>",
                    document_id="sha256:" + "a" * 64,
                    path=path,
                    batch_rows=1,
                    max_rows=1,
                )
            self.assertFalse(path.exists())

    def test_parquet_byte_budget_stops_and_removes_partial_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "document.parquet"
            with self.assertRaisesRegex(ValueError, "byte staging budget"):
                write_dom_parquet(
                    "<html><body><div>content</div></body></html>",
                    document_id="sha256:" + "a" * 64,
                    path=path,
                    batch_rows=1,
                    max_bytes=32,
                )
            self.assertFalse(path.exists())

    def test_parquet_projection_only_records_structural_output(self) -> None:
        scripts = "".join("<script></script>" for _ in range(12))
        html = (
            '<html><body><div id="root">Visible text</div>'
            '<a href="/docs">Docs</a><button class="load-more">Load more</button>'
            f"<form><input></form>{scripts}</body></html>"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = write_dom_parquet(
                html,
                document_id="sha256:" + "a" * 64,
                path=Path(temp_dir) / "document.parquet",
            )

        self.assertGreater(result.element_count, 0)
        self.assertGreater(result.size_bytes, 0)

    def test_schema_version_starts_at_one(self) -> None:
        self.assertEqual(DOM_SCHEMA_VERSION, 1)
        self.assertEqual(
            tuple(ELEMENT_COLUMNS),
            (
                "document_id",
                "element_index",
                "parent_index",
                "subtree_end_index",
                "depth",
                "tag",
                "namespace_uri",
                "attributes",
                "text_direct",
                "text_tail",
            ),
        )

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
        self.assertEqual(rows[4].subtree_end_index, 7)
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
                        href="https://www.example.com/assets/guide?a=1&b=2",
                        text="Nested bold after tail",
                        title="Read",
                        base_domain="example.com",
                    )
                ],
                "external": [
                    _link_payload(
                        href="https://outside.example/path",
                        text="External",
                        title="",
                        base_domain="outside.example",
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


def _link_payload(
    *,
    href: str,
    text: str,
    title: str,
    base_domain: str,
) -> dict[str, object]:
    return {
        "href": href,
        "text": text,
        "title": title,
        "base_domain": base_domain,
        "head_data": None,
        "head_extraction_status": None,
        "head_extraction_error": None,
        "intrinsic_score": 0.0,
        "contextual_score": None,
        "total_score": None,
    }


if __name__ == "__main__":
    unittest.main()
