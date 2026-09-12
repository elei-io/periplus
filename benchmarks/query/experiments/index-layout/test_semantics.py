"""The page-text contract and original offsets used by this experiment."""
import unittest
import random
from types import SimpleNamespace

from compare import page_words
from map_probe import map_page
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization import registry  # Discover declarations before importing one directly.
from periplus.materialization.projections.html_elements import project


def mapped(html):
    nodes, elements = parse_document(html)
    context = SimpleNamespace(content_output_hashes={'fixture'}, parsed_nodes_by_content={'fixture': nodes}, parsed_elements_by_content={'fixture': elements})
    rows = project(context).to_pylist()
    root = next(row for row in rows if row['parent_index'] is None)
    words = list(page_words(root['text']))
    result = {(term, row['tag']) for term, lo, hi in words for row in rows if row['text_start'] <= lo and hi <= row['text_end']}
    return words, result


class PageSemanticsTests(unittest.TestCase):
    def test_mapping_matches_brute_force_for_boundaries_and_repetition(self):
        rng = random.Random(1742)
        for _ in range(100):
            words = list(page_words('🐒 cat catfish Straße cafe\u0301 fish cat'))
            elements = [(node, *sorted((rng.randrange(40), rng.randrange(40)))) for node in range(50)]
            expected = {}
            for term, start, end in words:
                for node, lo, hi in elements:
                    if lo <= start and end <= hi:
                        expected.setdefault(term, set()).add(node)
            actual = dict(map_page(words, elements))
            self.assertEqual(actual, {term: sorted(nodes) for term, nodes in expected.items()})
        self.assertEqual(map_page([], [(0, 0, 0)]), [])
        self.assertEqual(map_page(list(page_words('cat')), []), [])

    def test_inline_split_uses_page_word_without_inventing_span_word(self):
        words, result = mapped('<p>cat<span>fish</span></p>')
        self.assertEqual(words, [('catfish', 0, 7)])
        self.assertIn(('catfish', 'p'), result)
        self.assertNotIn(('catfish', 'span'), result)
        self.assertNotIn(('fish', 'span'), result)

    def test_whitespace_word_maps_to_span_and_enclosing_elements(self):
        _, result = mapped('<p>cat <span>fish</span></p>')
        self.assertIn(('fish', 'span'), result)
        self.assertIn(('fish', 'p'), result)

    def test_unicode_positions_refer_to_original_code_points(self):
        self.assertEqual(list(page_words('🐒 Straße cafe\u0301')), [('strasse', 2, 8), ('café', 9, 14)])
        _, result = mapped('<p>🐒 <span>Straße</span> <b>cafe\u0301</b></p>')
        self.assertIn(('strasse', 'span'), result)
        self.assertIn(('café', 'b'), result)

    def test_title_is_text_and_metadata_attributes_are_not(self):
        words, result = mapped('<title>Title </title><meta name="description" content="secret"><p>Body</p>')
        self.assertEqual([word for word, _, _ in words], ['title', 'body'])
        self.assertIn(('title', 'title'), result)
        self.assertFalse(any(word == 'secret' for word, _ in result))

    def test_combining_mark_across_elements_belongs_to_parent(self):
        words, result = mapped('<p>cafe<span>\u0301</span></p>')
        self.assertEqual(words, [('café', 0, 5)])
        self.assertIn(('café', 'p'), result)
        self.assertNotIn(('café', 'span'), result)


if __name__ == '__main__':
    unittest.main()
