from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from control.catalogue_materializations.service import (
    materialization_eligibility,
)
from repository.catalogue.materializations import MaterializationError


class CatalogueMaterializationEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference_id = uuid4()
        self.reference = SimpleNamespace(ducklake_view_uuid=uuid4())
        self.source_view = SimpleNamespace(
            sql="SELECT document_id, url FROM documents"
        )
        self.session = Mock()
        self.session.scalar.return_value = self.reference
        self.store = Mock()
        self.store.catalogue = object()
        self.store.table_identity.return_value = SimpleNamespace(
            table_name="documents"
        )

    def check(self):
        with patch(
            "control.catalogue_materializations.service.CatalogueViewStore"
        ) as view_store:
            view_store.return_value.get.return_value = self.source_view
            return materialization_eligibility(
                self.session,
                self.store,
                view_reference_id=self.reference_id,
                source_table="documents",
                refresh_strategy="keyed",
                key_columns=["document_id"],
            )

    def test_eligible_view_uses_creation_validator_without_coverage_logging(
        self,
    ) -> None:
        result = self.check()

        self.assertTrue(result.eligible)
        self.assertEqual(result.diagnostics, [])
        self.store.validate_refresh_strategy.assert_called_once_with(
            source_table="documents",
            sql=self.source_view.sql,
            refresh_strategy="keyed",
            key_columns=("document_id",),
            coverage_source=None,
        )

    def test_incompatible_view_returns_a_non_throwing_diagnostic(self) -> None:
        self.store.validate_refresh_strategy.side_effect = MaterializationError(
            "This SQL cannot be materialized: unbounded relation."
        )

        result = self.check()

        self.assertFalse(result.eligible)
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(
            result.diagnostics[0].code,
            "materialization_incompatible",
        )
        self.assertEqual(result.diagnostics[0].severity, "error")
        self.assertIn("unbounded relation", result.diagnostics[0].message)


if __name__ == "__main__":
    unittest.main()
