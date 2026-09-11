"""Local node-posting semantics probe; never writes to the production lake."""
from collections import Counter
import json

from periplus.materialization import registry  # Initialize projection discovery first.

from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.projections.prose import _body_text, _EXCLUDED, _HTML
from periplus.materialization.tokenization import term_counts, tokenizer_metadata


def probe(source):
    nodes, _ = parse_document(source)
    body = next(n for n in nodes if n.node_type == 'element'
                and n.namespace == _HTML and n.name == 'body')
    rows = []
    skip_until = body.node_index + 1
    for node in nodes:
        if node.node_index < skip_until:
            continue
        if node.node_index >= body.subtree_end_index:
            break
        if node.node_type == 'element' and node.name in _EXCLUDED:
            skip_until = node.subtree_end_index
        elif node.node_type == 'text':
            for term, frequency in sorted(term_counts(node.value or '').items()):
                rows.append({'text': term, 'node_index': node.node_index,
                             'frequency': frequency,
                             'parent_tag': nodes[node.parent_index].name})
    totals = Counter()
    for row in rows:
        totals[row['text']] += row['frequency']
    content = term_counts(_body_text(nodes))
    return {'content_counts': dict(content), 'node_counts': dict(totals),
            'frequencies_equal': content == totals, 'matches': rows}


def main():
    fixtures = {
        'pricing': '<li><span>Monthly price</span><strong>$25</strong></li>',
        'repeated': '<p>monkey sees monkey</p><p>monkey sleeps</p>',
        'excluded': '<head><title>hidden</title></head><body><script>hidden</script><p>visible</p></body>',
        'inline_word': '<p>mon<strong>key</strong></p>',
        'inline_combining': '<p>caf<span>e</span>\u0301</p>',
        'multilingual': '<p>日本語の文章です。中文分词。 😀 café CAFÉ</p>',
    }
    results = {name: probe(source) for name, source in fixtures.items()}
    assert results['repeated']['content_counts']['monkey'] == 3
    assert results['repeated']['frequencies_equal']
    assert results['excluded']['node_counts'] == {'visible': 1}
    assert not results['inline_word']['frequencies_equal']
    assert not results['inline_combining']['frequencies_equal']
    print(json.dumps({'tokenizer': tokenizer_metadata(), 'cases': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
