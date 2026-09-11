"""Batch-local prose and term counts with text-node provenance."""
from collections import Counter
from dataclasses import dataclass

import icu

from periplus.materialization.dom.nodes import NodeRow
from periplus.materialization.tokenization import validate_tokenizer

_HTML = 'http://www.w3.org/1999/xhtml'
_EXCLUDED = frozenset({'script', 'style', 'template', 'noscript'})
_BLOCKS = frozenset('address article aside blockquote br caption dd details dialog div dl dt fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li main menu nav ol p pre section summary table tbody td tfoot th thead tr ul'.split())


@dataclass(frozen=True)
class SearchText:
    prose: str
    content_counts: Counter[str]
    node_counts: Counter[tuple[str, int]]


def body_parts(nodes: tuple[NodeRow, ...]):
    body = next((n for n in nodes if n.node_type == 'element'
                 and n.namespace == _HTML and n.name == 'body'), None)
    if body is None:
        return
    ends = []
    skip_until = body.node_index + 1
    for node in nodes:
        if node.node_index < skip_until:
            continue
        if node.node_index >= body.subtree_end_index:
            break
        while ends and ends[-1] <= node.node_index:
            yield ' ', None
            ends.pop()
        if node.node_type == 'element':
            if node.name in _EXCLUDED:
                skip_until = node.subtree_end_index
                continue
            if node.namespace == _HTML and node.name in _BLOCKS:
                yield ' ', None
                ends.append(node.subtree_end_index)
        elif node.node_type == 'text':
            yield node.value or '', node.node_index


def build_search_text(nodes: tuple[NodeRow, ...]) -> SearchText:
    validate_tokenizer()
    # Preserve exactly prose's split/join whitespace semantics, without retaining
    # source HTML or creating an occurrence object for every repeated term.
    chars, owners = [], []
    pending_space = False
    for text, owner in body_parts(nodes):
        for char in text:
            if char.isspace():
                pending_space = bool(chars)
                continue
            if pending_space:
                chars.append(' ')
                owners.append(None)
                pending_space = False
            chars.append(char)
            owners.append(owner)
    prose = ''.join(chars)
    normalizer = icu.Normalizer2.getNFCInstance()
    normalized_parts, normalized_owners = [], []
    segment, segment_owners = [], set()

    def flush():
        if not segment:
            return
        normalized = normalizer.normalize(''.join(segment))
        normalized_parts.append(normalized)
        provenance = frozenset(segment_owners)
        # BreakIterator offsets count UTF-16 units, including surrogate pairs.
        normalized_owners.extend([provenance] * (len(normalized.encode('utf-16-le')) // 2))
        segment.clear()
        segment_owners.clear()

    for char, owner in zip(chars, owners, strict=True):
        folded = icu.UnicodeString(char)
        folded.foldCase()
        for folded_char in str(folded):
            if normalizer.hasBoundaryBefore(folded_char):
                flush()
            segment.append(folded_char)
            if owner is not None:
                segment_owners.add(owner)
    flush()
    normalized = icu.UnicodeString(''.join(normalized_parts))
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(normalized)
    start = iterator.first()
    content_counts, node_counts = Counter(), Counter()
    for end in iterator:
        if iterator.getRuleStatus() >= 100:
            term = str(normalized[start:end])
            content_counts[term] += 1
            contributing = set().union(*normalized_owners[start:end])
            for owner in contributing:
                node_counts[term, owner] += 1
        start = end
    return SearchText(prose, content_counts, node_counts)
