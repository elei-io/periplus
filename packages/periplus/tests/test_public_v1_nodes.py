"""Semantic fixtures for the public document-wide node contract."""
import unittest

from periplus.materialization.dom.nodes import parse_document


class PublicV1NodesTests(unittest.TestCase):
    def test_mixed_content_and_subtree_boundaries(self):
        nodes, elements = parse_document('<!doctype html><!--before--><p>Hello <strong>world</strong>!<!-- greeting --></p>')
        paragraph = next(e for e in elements if e.tag == 'p')
        strong = next(e for e in elements if e.tag == 'strong')
        children = [n for n in nodes if n.parent_index == paragraph.element_index]
        self.assertEqual([(n.node_type, n.value) for n in children],
                         [('text', 'Hello '), ('element', None), ('text', '!'), ('comment', ' greeting ')])
        self.assertEqual([n.sibling_index for n in children], [0, 1, 2, 3])
        self.assertEqual(paragraph.text_direct, 'Hello !')
        self.assertEqual(strong.parent_index, paragraph.element_index)
        subtree = nodes[paragraph.element_index:paragraph.subtree_end_index]
        self.assertEqual(''.join(n.value for n in subtree if n.node_type == 'text'), 'Hello world!')
        self.assertEqual(nodes[0].node_type, 'document')
        self.assertEqual(nodes[0].subtree_end_index, len(nodes))
        self.assertTrue(any(n.node_type == 'doctype' for n in nodes))
        self.assertTrue(any(n.value == 'before' for n in nodes))
        for e in elements:
            n = nodes[e.element_index]
            self.assertEqual((n.node_type, n.name, n.parent_index), ('element', e.tag, e.parent_index))
            self.assertEqual(n.subtree_end_index, e.subtree_end_index)

    def test_leaf_elements_keep_exclusive_boundaries(self):
        nodes, elements = parse_document('<div><br><img src="x"><span></span></div>')
        leaves = [e for e in elements if e.tag in {'br', 'img', 'span'}]
        self.assertEqual(len(leaves), 3)
        for element in leaves:
            self.assertEqual(element.subtree_end_index, element.element_index + 1)
            self.assertEqual(nodes[element.element_index].subtree_end_index,
                             element.subtree_end_index)
        parent = next(e for e in elements if e.tag == 'div')
        self.assertEqual(parent.subtree_end_index, leaves[-1].subtree_end_index)

    def test_closing_records_preserve_preorder_and_parent_text(self):
        nodes, elements = parse_document(
            '<main>A<section id="s">B<b>C</b>D</section>E<hr>F</main>'
        )
        self.assertEqual([n.node_index for n in nodes], list(range(len(nodes))))
        self.assertEqual([e.tag for e in elements],
                         ['html', 'head', 'body', 'main', 'section', 'b', 'hr'])
        self.assertEqual([e.element_index for e in elements],
                         sorted(e.element_index for e in elements))
        by_tag = {e.tag: e for e in elements}
        self.assertEqual(by_tag['main'].text_direct, 'AEF')
        self.assertEqual(by_tag['section'].text_direct, 'BD')
        self.assertEqual(by_tag['section'].attributes, {'id': 's'})
        self.assertEqual(by_tag['b'].text_direct, 'C')
        for element in elements:
            self.assertEqual(element.subtree_end_index,
                             nodes[element.element_index].subtree_end_index)

    def test_entities_do_not_split_text_identity(self):
        nodes, elements = parse_document(b'<p>A&amp;B&#33;</p>')
        paragraph = next(e for e in elements if e.tag == 'p')
        children = [n for n in nodes if n.parent_index == paragraph.element_index]
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].value, 'A&B!')

    def test_namespace_and_attribute_identity(self):
        nodes, elements = parse_document('<svg><a xlink:href="/x">x</a></svg>')
        anchor = next(e for e in elements if e.tag == 'a')
        self.assertEqual(anchor.namespace_uri, 'http://www.w3.org/2000/svg')
        self.assertEqual(anchor.attributes['{http://www.w3.org/1999/xlink}href'], '/x')
        self.assertEqual(nodes[anchor.element_index].namespace, anchor.namespace_uri)
