"""Idempotent system-provided catalogue views."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_int
from control.catalogue_materializations.service import put_for_view
from repository.catalogue.materializations import MaterializationStore
from repository.catalogue.views import CatalogueViewStore

from .models import CatalogueViewReference
from .service import adopt_reference, create_reference

PAGE_LINKS_SQL = r"""
WITH links AS (
  SELECT c.crawl_id, c.document_id, c.captured_at, e.element_index,
    c.page_url AS source_url, c.url_scheme AS source_scheme,
    c.url_host AS source_host, c.url_registrable_domain AS source_registrable_domain,
    c.url_port AS source_port, c.url_path AS source_path, c.url_query AS source_query,
    get_attribute(e.attributes, 'href') AS href,
    resolve_url(c.page_url, get_attribute(e.attributes, 'href')) AS url,
    nullif(readable_text(e.document_id, e.element_index), '') AS anchor_text,
    CASE WHEN nullif(trim(get_attribute(e.attributes, 'rel')), '') IS NOT NULL
      THEN regexp_split_to_array(lower(trim(get_attribute(e.attributes, 'rel'))), '\s+') END AS rel,
    nullif(get_attribute(e.attributes, 'target'), '') AS target
  FROM main.crawls c JOIN main.elements e USING (document_id)
  WHERE e.tag = 'a' AND has_attribute(e.attributes, 'href')
), parsed AS (
  SELECT *,
    lower(regexp_extract(url, '^([A-Za-z][A-Za-z0-9+.-]*):', 1)) AS scheme,
    lower(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/?#]*@)?(\[[^]]+\]|[^:/?#]+)', 1)) AS host,
    coalesce(nullif(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]*:([0-9]+)', 1), '')::INTEGER,
      CASE lower(regexp_extract(url, '^([A-Za-z][A-Za-z0-9+.-]*):', 1)) WHEN 'http' THEN 80 WHEN 'https' THEN 443 ELSE 0 END) AS port,
    coalesce(nullif(regexp_extract(url, '^[A-Za-z][A-Za-z0-9+.-]*://[^/?#]+([^?#]*)', 1), ''), '/') AS path,
    regexp_extract(url, '\?([^#]*)', 1) AS query,
    regexp_extract(url, '#(.*)$', 1) AS fragment
  FROM links
), enriched AS (
  SELECT *, CAST(CASE WHEN query <> '' THEN (
    SELECT json_group_object(param_name, param_values) FROM (
      SELECT url_decode(replace(split_part(part, '=', 1), '+', ' ')) AS param_name,
        list(url_decode(replace(CASE WHEN contains(part, '=') THEN substring(part, strpos(part, '=') + 1) ELSE '' END, '+', ' ')) ORDER BY ordinal) AS param_values
      FROM unnest(string_split(query, '&')) WITH ORDINALITY AS params(part, ordinal)
      GROUP BY param_name
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
        reference_id = existing.id
    assert reference_id is not None
    put_for_view(
        session, MaterializationStore(catalogue), view_reference_id=reference_id,
        name="page_links", display_name="Page links",
        description="Crawl-scoped durable page links for graph edges.",
        refresh_mode="scope_incremental", scope_kind="crawl", scope_column="crawl_id",
        live_enabled=True, backfill_enabled=True,
        backfill_scopes_per_minute=get_int("ATLAS_MATERIALIZATION_BACKFILL_SCOPES_PER_MINUTE"),
        partition_column="captured_at",
    )
