"""A newly added projection must stay invisible until the complete atomic swap."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from periplus.materialization.registry import PROJECTIONS
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.public import install_public_catalogue


class ProjectionAdditionTests(unittest.TestCase):
    def test_setup_preserves_old_view_and_activation_is_atomic_and_replayable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with Catalogue(CatalogueConfig('periplus', str(root/'meta.duckdb'), str(root/'data'), 'ducklake')) as catalogue:
                catalogue.bootstrap()
                c = catalogue.trusted_connection
                # Reconstruct the previous complete generation without JSON-LD storage.
                old = (Path(__file__).resolve().parents[3] / 'docs/query-investigations/jsonld-layout/baseline.sql').read_text()
                c.execute(old)
                c.execute('DROP TABLE material.html_jsonld')
                c.execute("INSERT INTO material.html_nodes (content_sha256,node_index,parent_index,subtree_end_index,depth,sibling_index,name,tag,namespace,attributes,text_direct,node_type) VALUES ('a',3,NULL,4,0,0,'script','script','http://www.w3.org/1999/xhtml',MAP {'type':'application/ld+json'},'{\"name\":\"kept\"}','element')")
                before = c.execute('SELECT * FROM public_v1.html_jsonld').fetchall()
                self.assertEqual(len(before), 1)
                for _ in range(2):
                    self.assertFalse(catalogue._active_registry_matches())
                    catalogue.bootstrap()
                    self.assertEqual(c.execute('SELECT * FROM public_v1.html_jsonld').fetchall(), before)
                    self.assertEqual(c.execute("SELECT count(*) FROM duckdb_tables() WHERE schema_name='material' AND table_name='html_jsonld'").fetchone()[0], 0)
                generations = {}
                for spec in PROJECTIONS:
                    name = f'_periplus_rebuild_{spec.name}_guard'
                    catalogue.create_materialization_generation(spec.relation, name)
                    generations[spec.relation] = name
                    if spec.name == 'html_jsonld':
                        c.execute(f'INSERT INTO material.{name} SELECT * FROM public_v1.html_jsonld')
                    else:
                        c.execute(f'INSERT INTO material.{name} SELECT * FROM material.{spec.name}')
                with self.assertRaisesRegex(RuntimeError, 'rollback proof'):
                    with catalogue.remote_transaction():
                        catalogue.activate_materialization_generations(generations, activation_id='guard', transaction=False)
                        install_public_catalogue(catalogue, transaction=False)
                        raise RuntimeError('rollback proof')
                self.assertEqual(c.execute('SELECT * FROM public_v1.html_jsonld').fetchall(), before)
                self.assertFalse(catalogue._active_registry_matches())
                with catalogue.remote_transaction():
                    catalogue.activate_materialization_generations(generations, activation_id='guard', transaction=False)
                    install_public_catalogue(catalogue, transaction=False)
                self.assertTrue(catalogue._active_registry_matches())
                catalogue.validate_schema()
                self.assertEqual(c.execute('SELECT * FROM public_v1.html_jsonld').fetchall(), before)
                # A lost control acknowledgement may repeat activation after the lake commit.
                catalogue.activate_materialization_generations(generations, activation_id='guard')
                self.assertEqual(c.execute('SELECT * FROM public_v1.html_jsonld').fetchall(), before)
                catalogue.finalize_materialization_activation(generations, activation_id='guard')
