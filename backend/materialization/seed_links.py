from __future__ import annotations

import logging

from sqlalchemy import select

from config import get_int
from control.catalogue_queries.service import create_query
from control.catalogue_views.service import create_reference
from control.materialized_views.models import MaterializedView
from control.materialized_views.service import create as create_materialized_view
from db.session import session_scope
from repository.catalogue.client import Catalogue
from repository.catalogue.config import catalogue_config_from_env
from repository.catalogue.materialized_views import MaterializedViewStore
from repository.catalogue.views import CatalogueViewStore

LINKS_QUERY = """
WITH links AS MATERIALIZED (
    SELECT
        e.document_id,
        e.element_index,
        e.attributes,
        d.created_at AS document_created_at
    FROM elements AS e
    JOIN documents AS d USING (document_id)
    WHERE e.document_id = $document_id
      AND e.tag = 'a'
      AND has_attribute(e.attributes, 'href')
)
SELECT
    document_id,
    element_index,
    get_attribute(attributes, 'href') AS href,
    readable_text(document_id, element_index) AS anchor_text,
    document_created_at
FROM links
""".strip()

LINKS_VIEW = """
SELECT
    c.crawl_id,
    c.document_id,
    c.page_url AS source_url,
    c.captured_at,
    l.element_index,
    l.href,
    resolve_url(c.page_url, l.href) AS destination_url,
    l.anchor_text,
    l.document_created_at
FROM {catalogue}.main.crawls AS c
JOIN {catalogue}.materialized.document_links AS l USING (document_id)
""".strip()


def seed() -> MaterializedView:
    with Catalogue(catalogue_config_from_env()) as catalogue, session_scope() as session:
        existing = session.scalar(
            select(MaterializedView).where(
                MaterializedView.name == "document_links",
                MaterializedView.archived_at.is_(None),
            )
        )
        if existing is not None:
            logging.info("materialized.document_links is already configured")
            session.expunge(existing)
            return existing

        saved = create_query(
            session,
            name="Document links by document",
            description=(
                "One bounded materialization scope: links extracted from one immutable "
                "HTML document."
            ),
            sql=LINKS_QUERY,
            change_note="Seed incremental document-links materialization",
        )
        create_materialized_view(
            session,
            MaterializedViewStore(catalogue),
            name="document_links",
            display_name="Document links",
            description=(
                "Incrementally maintained links keyed by immutable document and DOM "
                "element. Backfill and live ingestion use the same bounded job."
            ),
            query_revision_id=saved.current_revision_id,
            source_view_uuid=None,
            refresh_mode="scope_incremental",
            scope_kind="document",
            scope_column="document_id",
            live_enabled=True,
            backfill_enabled=True,
            backfill_scopes_per_minute=get_int(
                "ATLAS_MATERIALIZATION_BACKFILL_SCOPES_PER_MINUTE"
            ),
            partition_column="document_created_at",
        )
        model = session.scalar(
            select(MaterializedView).where(MaterializedView.name == "document_links")
        )
        if model is None:
            raise RuntimeError("document-links materialization was not created")

        view_store = CatalogueViewStore(catalogue)
        if not any(view.view_name == "links" for view in view_store.list()):
            create_reference(
                session,
                view_store,
                name="links",
                sql=LINKS_VIEW.format(
                    catalogue='"'
                    + catalogue.config.alias.replace('"', '""')
                    + '"'
                ),
                display_name="Links",
                description=(
                    "Convenience view joining incrementally materialized document links "
                    "to crawl observations."
                ),
                created_from_query_revision_id=saved.current_revision_id,
            )
        session.expunge(model)
        logging.info(
            "activated materialized.document_links at snapshot %s (%s)",
            model.activation_snapshot,
            model.id,
        )
        return model


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    seed()


if __name__ == "__main__":
    main()
