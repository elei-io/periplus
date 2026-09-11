"""Content-owned term frequencies, encoded using the generation dictionary."""
import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.projections.vocabulary import content_terms
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(PROJECTION.arrow_schema, (
        (context.dictionary_ids['vocabulary'][term], content_id, frequency)
        for content_id, counts in content_terms(context)
        for term, frequency in sorted(counts.items())
    ))


PROJECTION = ProjectionSpec(
    name='term_stat', ownership_grain='content',
    columns=(ProjectionColumn('term_id', pa.int64(), 'BIGINT', 'Identity in the generation vocabulary.', False),
             ProjectionColumn('content_sha256', pa.string(), 'VARCHAR', 'Immutable HTML content identity.', False),
             ProjectionColumn('frequency', pa.int64(), 'BIGINT', 'Occurrences in normalized body prose.', False)),
    partitioning=(), sort_order=('term_id ASC', 'content_sha256 ASC'), projector=project,
    description='One positive frequency per content and normalized search term.',
    identity_columns=('content_sha256', 'term_id'), dictionary_dependencies=('vocabulary',),
    validation_queries=(
        'SELECT count(*) FROM material.term_stat WHERE frequency <= 0 OR frequency IS NULL',
        'SELECT count(*) FROM material.term_stat p ANTI JOIN material.vocabulary v USING (term_id)',
    ),
)
