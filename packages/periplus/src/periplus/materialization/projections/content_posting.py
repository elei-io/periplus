"""Content-owned term frequencies, encoded using the generation dictionary."""
import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.projections.term import content_terms
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec


def project(context: VisitBatchContext) -> pa.Table:
    return table_from_rows(PROJECTION.arrow_schema, (
        (context.dictionary_ids['term'][term], content_id, frequency)
        for content_id, counts in content_terms(context)
        for term, frequency in sorted(counts.items())
    ))


PROJECTION = ProjectionSpec(
    name='content_posting', ownership_grain='content',
    columns=(ProjectionColumn('term_id', pa.int64(), 'BIGINT', 'Identity in the generation term.', False),
             ProjectionColumn('content_sha256', pa.string(), 'VARCHAR', 'Immutable HTML content identity.', False),
             ProjectionColumn('frequency', pa.int64(), 'BIGINT', 'Occurrences in normalized body prose.', False)),
    partitioning=(), sort_order=('term_id ASC', 'content_sha256 ASC'), projector=project,
    description='One positive frequency per content and normalized search term.',
    identity_columns=('content_sha256', 'term_id'), dictionary_dependencies=('term',),
    validation_queries=(
        'SELECT count(*) FROM material.content_posting WHERE frequency <= 0 OR frequency IS NULL',
        'SELECT count(*) FROM material.content_posting p ANTI JOIN material.term v USING (term_id)',
    ),
)
