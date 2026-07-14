"""Idempotent system-provided catalogue views."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_materializations.service import put_for_view, rebuild
from repository.catalogue.materializations import MaterializationStore
from repository.catalogue.macros import READABLE_BREAK_TAGS
from repository.catalogue.views import CatalogueViewStore

from .models import CatalogueViewReference
from .service import adopt_reference, create_reference, update_reference

PAGE_LINKS_RECIPE = "page-links-v4-use-crawls-only"
_READABLE_BREAK_TAGS_SQL = ", ".join(
    "'" + value.replace("'", "''") + "'" for value in READABLE_BREAK_TAGS
)

PAGE_LINKS_SQL = rf"""
WITH recipe AS (
  SELECT '{PAGE_LINKS_RECIPE}' AS version
), links AS (
  SELECT c.crawl_id, c.document_id, c.captured_at, e.element_index,
    e.subtree_end_index,
    c.page_url AS source_url, c.url_scheme AS source_scheme,
    c.url_host AS source_host, c.url_registrable_domain AS source_registrable_domain,
    c.url_port AS source_port, c.url_path AS source_path, c.url_query AS source_query,
    get_attribute(e.attributes, 'href') AS href,
    resolve_url(c.page_url, get_attribute(e.attributes, 'href')) AS url,
    CASE WHEN nullif(trim(get_attribute(e.attributes, 'rel')), '') IS NOT NULL
      THEN regexp_split_to_array(lower(trim(get_attribute(e.attributes, 'rel'))), '\s+') END AS rel,
    nullif(get_attribute(e.attributes, 'target'), '') AS target
  FROM main.crawls c JOIN main.elements e USING (document_id) CROSS JOIN recipe
  WHERE recipe.version = '{PAGE_LINKS_RECIPE}'
    AND c.purpose = 'use'
    AND e.tag = 'a' AND has_attribute(e.attributes, 'href')
), anchor_fragments AS (
  SELECT l.crawl_id, l.element_index,
    event.event_index, event.event_phase, event.depth, event.depth_phase,
    event.fragment
  FROM links l
  JOIN main.elements child
    ON child.document_id = l.document_id
   AND child.element_index BETWEEN l.element_index AND l.subtree_end_index
  CROSS JOIN LATERAL (
    VALUES
      (child.element_index, 0, child.depth, 0, child.text_direct),
      (child.subtree_end_index, 1, child.depth, 0,
        CASE WHEN child.element_index > l.element_index
          AND child.tag IN ({_READABLE_BREAK_TAGS_SQL}) THEN ' ' ELSE '' END),
      (child.subtree_end_index, 1, child.depth, 1,
        CASE WHEN child.element_index > l.element_index THEN child.text_tail ELSE '' END)
  ) AS event(event_index, event_phase, depth, depth_phase, fragment)
  WHERE event.fragment <> ''
), anchor_texts AS (
  SELECT crawl_id, element_index,
    nullif(trim(regexp_replace(
      string_agg(fragment, '' ORDER BY event_index, event_phase, depth DESC, depth_phase),
      '\s+', ' ', 'g'
    )), '') AS anchor_text
  FROM anchor_fragments
  GROUP BY crawl_id, element_index
), parsed AS (
  SELECT l.* EXCLUDE (subtree_end_index), a.anchor_text,
    lower(regexp_extract(url, '^([A-Za-z][A-Za-z0-9+.-]*):', 1)) AS scheme,
    lower(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/?#]*@)?(\[[^]]+\]|[^:/?#]+)', 1)) AS host,
    coalesce(nullif(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*:([0-9]+)', 1), '')::INTEGER,
      CASE lower(regexp_extract(url, '^([A-Za-z][A-Za-z0-9+.-]*):', 1)) WHEN 'http' THEN 80 WHEN 'https' THEN 443 ELSE 0 END) AS port,
    coalesce(nullif(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]+([^?#]*)', 1), ''), '/') AS path,
    regexp_extract(url, '\?([^#]*)', 1) AS query,
    regexp_extract(url, '#(.*)$', 1) AS fragment
  FROM links l LEFT JOIN anchor_texts a USING (crawl_id, element_index)
), enriched AS (
  SELECT *, CAST(CASE WHEN query <> '' THEN (
    SELECT json_group_object(param_name, param_values) FROM (
      SELECT url_decode(replace(split_part(part, '=', 1), '+', ' ')) AS param_name,
        list(url_decode(replace(CASE WHEN contains(part, '=') THEN substring(part, strpos(part, '=') + 1) ELSE '' END, '+', ' ')) ORDER BY ordinal) AS param_values
      FROM unnest(string_split(query, '&')) WITH ORDINALITY AS params(part, ordinal)
      GROUP BY param_name
      ORDER BY param_name
    ) grouped_params
  ) END AS JSON) AS query_params FROM parsed
)
SELECT crawl_id, document_id, captured_at, element_index,
  source_url, source_scheme, source_host, source_registrable_domain, source_port, source_path, source_query,
  href, url, anchor_text, rel, target, scheme, host, port, path, query, fragment, query_params,
  scheme IN ('http', 'https') AS is_http,
  scheme = source_scheme AND host = source_host AND port = source_port AS is_same_origin,
  host = source_host AS is_same_host, host = source_host AS is_internal,
  scheme = source_scheme AND host = source_host AND port = source_port AND path = source_path AS is_same_page,
  scheme = source_scheme AND host = source_host AND port = source_port AND path = source_path AND query <> source_query AS is_query_variant,
  scheme = source_scheme AND host = source_host AND port = source_port AND path = source_path AND query = source_query AND fragment <> '' AS is_fragment_reference
FROM enriched
ORDER BY crawl_id, element_index
""".strip()


def provision_system_views(session: Session, catalogue) -> None:
    existing = session.scalar(select(CatalogueViewReference).where(
        CatalogueViewReference.schema_name == "views",
        CatalogueViewReference.view_name == "page_links",
        CatalogueViewReference.archived_at.is_(None),
    ))
    view_store = CatalogueViewStore(catalogue)
    if existing is None:
        ducklake_view = next(
            (view for view in view_store.list() if view.view_name == "page_links"),
            None,
        )
        common = {
            "display_name": "Page links",
            "description": "Resolved page links with URL components and traversal classifications.",
            "provisioned_by": "system",
        }
        record = (
            adopt_reference(
                session, view_store, view_uuid=ducklake_view.view_uuid, **common
            )
            if ducklake_view is not None
            else create_reference(
                session, view_store, name="page_links", sql=PAGE_LINKS_SQL, **common
            )
        )
        reference_id = record.id
    else:
        if existing.provisioned_by != "system":
            raise RuntimeError("views.page_links is reserved for Atlas system provisioning")
        current = view_store.get(existing.ducklake_view_uuid)
        if current is None:
            raise RuntimeError("the system page_links view is missing from DuckLake")
        attached = session.scalar(
            select(CatalogueMaterialization).where(
                CatalogueMaterialization.view_reference_id == existing.id,
                CatalogueMaterialization.archived_at.is_(None),
            )
        )
        definition_sql = attached.source_sql if attached is not None else current.sql
        if PAGE_LINKS_RECIPE not in definition_sql:
            update_reference(
                session,
                view_store,
                existing,
                expected_uuid=existing.ducklake_view_uuid,
                sql=PAGE_LINKS_SQL,
                display_name="Page links",
                description=(
                    "Resolved page links with URL components and traversal classifications."
                ),
            )
        reference_id = existing.id
    assert reference_id is not None
    materialization_store = MaterializationStore(catalogue)
    record = put_for_view(
        session, materialization_store, view_reference_id=reference_id,
        name="page_links", display_name="Page links",
        description="Crawl-scoped durable page links for graph edges.",
        scope_kind="crawl", scope_column="crawl_id",
        backfill_scopes_per_minute=get_int("ATLAS_MATERIALIZATION_BACKFILL_SCOPES_PER_MINUTE"),
        partition_column="captured_at",
    )
    materialization = session.scalar(
        select(CatalogueMaterialization).where(
            CatalogueMaterialization.view_reference_id == reference_id,
            CatalogueMaterialization.archived_at.is_(None),
        )
    )
    if materialization is not None and materialization.source_state == "source_changed":
        rebuild(
            session,
            materialization_store,
            materialization,
            expected_uuid=record.ducklake_table_uuid,
        )
        materialization.live_enabled = True
