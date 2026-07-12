from inspect import signature
from unittest import TestCase

from control.data_schemas.models import DataSchema
from control.data_schemas.schemas import (
    DataSchemaDetailRecord,
    DataSchemaListRecord,
    DataSchemaSummary,
)
from control.data_schemas.service import create_data_schema, replace_data_schema
from control.query_schemas.models import QuerySchema
from control.query_schemas.schemas import QuerySchemaListRecord, QuerySchemaRecord
from control.query_schemas.service import upsert_query_schema
from control.url_matching import (
    UrlMatch,
    resolve_domain_url_match_for_url,
    resolve_url_match_for_url,
)


class SchemaProvenanceContractTests(TestCase):
    def test_postgres_models_do_not_store_task_run_provenance(self) -> None:
        self.assertNotIn("generated_by_task_run_id", DataSchema.__table__.columns)
        self.assertNotIn("generated_by_task_run_id", QuerySchema.__table__.columns)
        self.assertNotIn("created_by_task_run_id", UrlMatch.__table__.columns)
        self.assertNotIn("updated_by_task_run_id", UrlMatch.__table__.columns)

    def test_api_schemas_do_not_expose_task_run_provenance(self) -> None:
        self.assertNotIn("generated_by_task_run_id", DataSchemaDetailRecord.model_fields)
        self.assertNotIn("task_run_count", DataSchemaListRecord.model_fields)
        self.assertNotIn("task_run_count", DataSchemaDetailRecord.model_fields)
        self.assertNotIn("generated_by_task_run_id", QuerySchemaRecord.model_fields)
        self.assertNotIn("generated_by_task_run_id", QuerySchemaListRecord.model_fields)

    def test_summary_does_not_report_task_run_use_metrics(self) -> None:
        self.assertEqual(
            set(DataSchemaSummary.model_fields),
            {"total_schemas", "enabled_schemas", "failing_schemas"},
        )

    def test_services_do_not_accept_task_run_provenance(self) -> None:
        for service in (
            create_data_schema,
            replace_data_schema,
            upsert_query_schema,
            resolve_url_match_for_url,
            resolve_domain_url_match_for_url,
        ):
            with self.subTest(service=service.__name__):
                self.assertNotIn("task_run_id", signature(service).parameters)
