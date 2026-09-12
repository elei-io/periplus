"""Complete parsed-text indexing with structural phrase boundaries and provenance."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

import icu

from periplus.materialization.dom.nodes import NodeRow
from periplus.materialization.tokenization import term_tokens, validate_tokenizer

TEXT_BOUNDARY_POLICY = "all-text-inline-runs-v1"
_HTML = "http://www.w3.org/1999/xhtml"
# Structural rules, independent of CSS and visibility. Every other element is a
# boundary on entry and exit, including custom elements and foreign namespaces.
_INLINE = frozenset(
    [
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "i",
        "ins",
        "kbd",
        "label",
        "mark",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "time",
        "u",
        "var",
        "wbr",
    ]
)


@dataclass(frozen=True, slots=True)
class Occurrence:
    position: int
    node_indexes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SearchText:
    occurrences: dict[str, tuple[Occurrence, ...]]


def text_runs(nodes: tuple[NodeRow, ...]):
    """Yield text-node groups; never discard a text node based on its location."""
    ends: list[int] = []
    run: list[NodeRow] = []
    for node in nodes:
        while ends and ends[-1] <= node.node_index:
            if run:
                yield tuple(run)
                run.clear()
            ends.pop()
        if node.node_type == "element" and not (
            node.namespace == _HTML and node.name in _INLINE
        ):
            if run:
                yield tuple(run)
                run.clear()
            ends.append(node.subtree_end_index)
        elif node.node_type == "text":
            run.append(node)
    if run:
        yield tuple(run)


def _tokens(run: tuple[NodeRow, ...]):
    run = tuple(node for node in run if node.value)
    if len(run) == 1:
        owners = (run[0].node_index,)
        for term in term_tokens(run[0].value):
            yield term, owners
        return
    if run and all(node.value.isascii() for node in run):
        # ASCII folding preserves UTF-16 offsets. Resolve ownership at token
        # boundaries, without allocating a provenance set per source character.
        text = icu.UnicodeString("".join(node.value for node in run))
        text.foldCase()
        ends, total = [], 0
        for node in run:
            total += len(node.value)
            ends.append(total)
        iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
        iterator.setText(text)
        start, cache = iterator.first(), {}
        for end in iterator:
            if iterator.getRuleStatus() >= 100:
                bounds = (bisect_right(ends, start), bisect_left(ends, end) + 1)
                if bounds not in cache:
                    cache[bounds] = tuple(
                        n.node_index for n in run[bounds[0] : bounds[1]]
                    )
                yield str(text[start:end]), cache[bounds]
            start = end
        return
    normalizer = icu.Normalizer2.getNFCInstance()
    parts: list[str] = []
    owners: list[frozenset[int]] = []
    segment: list[str] = []
    segment_owners: set[int] = set()

    def flush():
        if not segment:
            return
        text = normalizer.normalize("".join(segment))
        parts.append(text)
        owners.extend(
            [frozenset(segment_owners)] * (len(text.encode("utf-16-le")) // 2)
        )
        segment.clear()
        segment_owners.clear()

    for node in run:
        for char in node.value or "":
            folded = icu.UnicodeString(char)
            folded.foldCase()
            for ch in str(folded):
                if normalizer.hasBoundaryBefore(ch):
                    flush()
                segment.append(ch)
                segment_owners.add(node.node_index)
    flush()
    text = icu.UnicodeString("".join(parts))
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(text)
    start = iterator.first()
    for end in iterator:
        if iterator.getRuleStatus() >= 100:
            yield str(text[start:end]), tuple(sorted(set().union(*owners[start:end])))
        start = end


def build_search_text(nodes: tuple[NodeRow, ...]) -> SearchText:
    validate_tokenizer()
    occurrences: dict[str, list[Occurrence]] = {}
    position = 0
    for run in text_runs(nodes):
        for term, owners in _tokens(run):
            occurrences.setdefault(term, []).append(Occurrence(position, owners))
            position += 1
        # An unused ordinal prevents consecutive-position phrase matches from
        # crossing structural runs, even when the intervening text is empty.
        position += 1
    return SearchText({term: tuple(items) for term, items in occurrences.items()})
