"""Content-clustered term occurrences touching individual text nodes."""
import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(PROJECTION.arrow_schema, (
        (context.dictionary_ids['term'][term], content_id, node_index, frequency)
        for content_id in sorted(context.content_output_hashes)
        for (term, node_index), frequency in sorted(context.search_text(content_id).node_counts.items())
    ))


PROJECTION = ProjectionSpec(
    name='node_posting', ownership_grain='content',
    columns=(
        ProjectionColumn('term_id', pa.int64(), 'BIGINT', 'Generation term identity.', False),
        ProjectionColumn('content_sha256', pa.string(), 'VARCHAR', 'Immutable content identity.', False),
        ProjectionColumn('node_index', pa.int32(), 'INTEGER', 'Contributing text node.', False),
        ProjectionColumn('frequency', pa.int64(), 'BIGINT', 'Occurrences touching this node; not additive across nodes.', False),
    ),
    partitioning=(), sort_order=('content_sha256 ASC', 'term_id ASC', 'node_index ASC'),
    projector=project, description='Term occurrences mapped to contributing body text nodes.',
    identity_columns=('content_sha256', 'term_id', 'node_index'), dictionary_dependencies=('term',),
    validation_queries=(
        'SELECT count(*) FROM material.node_posting WHERE frequency <= 0 OR frequency IS NULL',
        'SELECT count(*) FROM material.node_posting p ANTI JOIN material.term t USING (term_id)',
        "SELECT count(*) FROM material.node_posting p ANTI JOIN (SELECT content_sha256, node_index FROM material.html_nodes WHERE node_type = 'text') n USING (content_sha256, node_index)",
    ),
)
