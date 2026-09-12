"""Immutable term/content posting lists from one ICU pass over parsed page text."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterator

import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import PartitionTransform, ProjectionColumn, ProjectionSpec


def validate_tokenizer() -> None:
    import icu

    actual = (icu.VERSION, icu.ICU_VERSION, icu.UNICODE_VERSION)
    if actual != ("2.16.2", "77.1", "16.0"):
        raise RuntimeError(f"html_terms requires PyICU 2.16.2 / ICU 77.1 / Unicode 16.0; got {actual}")


def page_terms(text: str) -> Iterator[tuple[str, int, int]]:
    """Normalize term keys while retaining original Unicode code-point spans."""
    import icu

    validate_tokenizer()
    original = icu.UnicodeString(text)
    iterator = icu.BreakIterator.createWordInstance(icu.Locale.getRoot())
    iterator.setText(original)
    normalizer = icu.Normalizer2.getNFCInstance()
    start_utf16 = iterator.first()
    start_cp = 0
    for end_utf16 in iterator:
        piece = original[start_utf16:end_utf16]
        end_cp = start_cp + len(str(piece))
        if iterator.getRuleStatus() >= 100:
            piece.foldCase()
            yield normalizer.normalize(piece), start_cp, end_cp
        start_utf16, start_cp = end_utf16, end_cp


def project(context: VisitBatchContext) -> pa.Table:
    def rows():
        for content_id in sorted(context.content_output_hashes):
            nodes = context.parsed_nodes_by_content[content_id]
            prefix = [0]
            pieces = []
            for node in nodes:
                value = (node.value or "") if node.node_type == "text" else ""
                pieces.append(value)
                prefix.append(prefix[-1] + len(value))
            words = list(page_terms("".join(pieces)))
            starts = [word[1] for word in words]
            ends = [word[2] for word in words]
            terms = [word[0] for word in words]
            postings: dict[str, list[int]] = defaultdict(list)
            for element in context.parsed_elements_by_content[content_id]:
                node = nodes[element.element_index]
                first = bisect_left(starts, prefix[node.node_index])
                last = bisect_right(ends, prefix[node.subtree_end_index])
                for term in set(terms[first:last]):
                    postings[term].append(node.node_index)
            for term in sorted(postings):
                yield term, content_id, sorted(postings[term])
    return table_from_rows(PROJECTION.arrow_schema, rows())


PROJECTION = ProjectionSpec(
    name="html_terms", ownership_grain="content",
    columns=(
        ProjectionColumn("term", pa.string(), "VARCHAR", "ICU page word, case folded then NFC normalized.", False),
        ProjectionColumn("content_sha256", pa.string(), "VARCHAR", "Immutable source identity.", False),
        ProjectionColumn("node_indexes", pa.list_(pa.int32()), "INTEGER[]", "Sorted distinct elements fully containing a page occurrence of the term.", False),
    ),
    partitioning=(PartitionTransform("bucket", "term", buckets=8),),
    sort_order=("term ASC", "content_sha256 ASC"), projector=project,
    description="Page-once ICU term/content postings with immutable element lists; metadata attributes excluded.",
    identity_columns=("term", "content_sha256"),
    parquet_row_group_size=2048,
    implementation_dependencies=("periplus.materialization.dom.nodes", "periplus.materialization.dom.lexbor"),
    validation_queries=(
        "SELECT count(*) FROM material.html_terms WHERE term = '' OR len(node_indexes) = 0 OR node_indexes <> list_sort(list_distinct(node_indexes)) OR list_min(node_indexes) < 0",
    ),
)
