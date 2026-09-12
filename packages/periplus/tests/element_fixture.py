"""Real element projection and catalogue for SQL contract tests."""
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.registry import BY_NAME
from periplus.platform.catalogue.public import install_public_catalogue
from periplus.platform.catalogue.schema import expected_columns
from periplus.platform.catalogue.client import _column_type
from test_public_catalogue import _LocalCatalogue

def catalogue():
    c = _LocalCatalogue()
    for schema in ('ingest', 'material'):
        c.connection.execute(f'CREATE SCHEMA {schema}')
    for relation, columns in expected_columns().items():
        definitions = ', '.join(f'"{name}" {_column_type(col)}' for name, col in columns.items())
        c.connection.execute(f'CREATE TABLE {relation.qualified} ({definitions})')
    install_public_catalogue(c)
    return c

def seed(connection, html, content='fixture'):
    nodes, elements = parse_document(html)
    context = VisitBatchContext((), (), (), {content: elements}, {content: nodes}, {}, frozenset({content}))
    connection.register('fixture_elements', BY_NAME['html_elements'].rows(context))
    try:
        connection.execute('INSERT INTO material.html_elements SELECT * FROM fixture_elements')
    finally:
        connection.unregister('fixture_elements')
    return nodes, elements
