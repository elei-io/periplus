import unittest
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.html_content import html_content


class HtmlContentTests(unittest.TestCase):
    def test_unicode_descendant_spans_preserve_text_without_added_separators(self):
        nodes, elements = parse_document('<div>A😀<span>猫</span>Z</div>')
        content = html_content('a' * 64, nodes, elements)
        div = next(row for row in content.elements if row.tag == 'div')
        span = next(row for row in content.elements if row.tag == 'span')
        self.assertEqual(content.document_text[div.text_start:div.text_end], 'A😀猫Z')
        self.assertEqual(content.document_text[span.text_start:span.text_end], '猫')
        self.assertEqual(div.text_direct, 'A😀Z')
        self.assertEqual(span.parent_index, div.node_index)

    def test_nested_elements_share_one_text_body(self):
        nodes, elements = parse_document('<div>' * 100 + 'payload' + '</div>' * 100)
        content = html_content('b' * 64, nodes, elements)
        self.assertEqual(content.document_text, 'payload')
        divs = [row for row in content.elements if row.tag == 'div']
        self.assertEqual(len(divs), 100)
        self.assertEqual({(row.text_start, row.text_end) for row in divs}, {(0, 7)})
        self.assertEqual(sum(len(row.text_direct) for row in content.elements), 7)


if __name__ == '__main__':
    unittest.main()
