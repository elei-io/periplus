"""Generation-owned term dictionary; compact IDs are reserved before file preparation."""
import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext, table_from_rows
from periplus.materialization.registry import ProjectionColumn, ProjectionSpec
from periplus.materialization.tokenization import validate_tokenizer

validate_tokenizer()


def content_terms(context: VisitBatchContext):
    for content_id in sorted(context.content_output_hashes):
        yield content_id, context.search_text(content_id).content_counts


def project(context: VisitBatchContext) -> pa.Table:
    terms = {term for _, counts in content_terms(context) for term in counts}
    return table_from_rows(PROJECTION.input_schema, ((term,) for term in sorted(terms)))


PROJECTION = ProjectionSpec(
    name='term', ownership_grain='generation',
    columns=(ProjectionColumn('text', pa.string(), 'VARCHAR', 'Normalized ICU search term.', False),
             ProjectionColumn('term_id', pa.int64(), 'BIGINT', 'Generation-local dictionary identity.', False)),
    partitioning=(), sort_order=('text ASC',), projector=project,
    description='Shared append-only term dictionary; unused entries are retained until a full rebuild.',
    identity_columns=('text',), dictionary_key='text', dictionary_id='term_id',
    implementation_dependencies=('periplus.materialization.tokenization', 'periplus.materialization.search_text'),
    validation_queries=(
        'SELECT count(*) - count(DISTINCT term_id) FROM material.term',
        'SELECT count(*) FROM material.term WHERE term_id <= 0 OR term_id IS NULL OR text IS NULL',
    ),
)
