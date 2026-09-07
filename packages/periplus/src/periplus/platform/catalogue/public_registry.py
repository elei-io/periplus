"""Authoritative manifest for the narrow public evidence catalogue."""

from periplus.platform.catalogue.public import CatalogueObject
from periplus.platform.catalogue.helpers import HELPERS


def _comments(
    *items: tuple[str, str],
) -> tuple[tuple[str, str], ...]:
    return items


_HTML = frozenset({"material.html_elements"})
_LINKS = frozenset({"material.link_occurrences"})
_HTML_COLUMNS = (
    "content_id", "element_index", "parent_index", "subtree_end_index",
    "depth", "child_index", "tag", "namespace", "attributes",
    "text_direct", "text_tail",
)
_HTML_COMMENTS = _comments(
    ("content_id", "Immutable HTML content identity."),
    ("element_index", "Zero-based depth-first document position."),
    ("parent_index", "Parent element index, null for the root."),
    ("subtree_end_index", "Exclusive end of this element subtree."),
    ("depth", "Element depth from the document root."),
    ("child_index", "Zero-based position among element siblings."),
    ("tag", "Normalized local tag name."),
    ("namespace", "Normalized element namespace."),
    ("attributes", "Attribute names and string values."),
    ("text_direct", "Text directly inside this element."),
    ("text_tail", "Text following this element within its parent."),
)

_LINEAGE_OBJECTS = tuple(
    CatalogueObject(
        "view", name, resource, tuple(column for column, _ in columns),
        comment=description, column_comments=columns,
    )
    for name, resource, description, columns in (
        ("collection", "views/003_collection.sql", "Public collection intent with its optional terminal outcome.", (
            ("collection_id", "Finite collection request identity."),
            ("requested_at", "Time the collection definition was accepted."),
            ("specification", "Frozen SQL selection, scope, and page limits."),
            ("settled_at", "Time the collection settled, if available."),
            ("outcome", "Why the collection settled."),
            ("seed_provenance", "Frozen seed snapshot, query identity, selection time, and candidate digest."),
            ("consumed_pages", "Request page units consumed by dispatch or reuse."),
            ("supplied_pages", "Successfully supplied request URLs."),
            ("failed_pages", "Request URLs with terminal acquisition failure."),
        )),
        ("fulfillment", "views/004_fulfillment.sql", "Request URL results referencing independently owned observations.", (
            ("fulfillment_id", "Stable identity of the request URL result."),
            ("collection_id", "Collection receiving this result."),
            ("observation_id", "Observation supplying this result."),
            ("requested_url", "Normalized URL selected by the collection."),
            ("parent_observation_id", "Winning parent evidence, when applicable."),
            ("depth", "First committed traversal depth."),
            ("rule_id", "Frozen selection rule identity."),
            ("mode", "Acquired, shared, or reused result."),
            ("decided_at", "Time this fulfillment was accepted."),
        )),
        ("acquisition_reason", "views/005_acquisition_reason.sql", "Public causal reasons frozen before acquisition dispatch.", (
            ("reason_id", "Stable identity of the causal reason."),
            ("observation_id", "Observation this dispatch produces."),
            ("collection_id", "Request causing capture; null for background."),
            ("parent_observation_id", "Parent observation leading to discovery."),
            ("reason", "Collection or background selection."),
            ("policy_version", "Effective dispatch policy version."),
            ("rule_id", "Selection rule identity."),
            ("selection_provenance", "Historical-check snapshot, query identity, and selection policy version."),
            ("decided_at", "Time this reason was frozen."),
        )),
    )
)

PUBLIC_OBJECTS = (
    *_LINEAGE_OBJECTS,
    CatalogueObject(
        "view", "observation", "views/001_observation.sql",
        (
            "observation_id", "requested_url", "effective_url",
            "observed_at", "outcome", "http_status_code", "content_id",
            "source_kind", "source_system", "source_dataset",
            "source_record_id",
        ),
        comment="Terminal URL observations with optional retained content evidence.",
        column_comments=_comments(
            ("observation_id", "Unique identity of this terminal observation."),
            ("requested_url", "Exact URL Periplus attempted to visit."),
            ("effective_url", "Final URL after navigation or redirects."),
            ("observed_at", "Time retained content was captured, when present."),
            ("outcome", "Final logical observation outcome."),
            ("http_status_code", "Final HTTP status when available."),
            ("content_id", "Retained immutable content identity, when present."),
            ("source_kind", "Native Periplus or external source kind."),
            ("source_system", "External source system, when applicable."),
            ("source_dataset", "External dataset, when applicable."),
            ("source_record_id", "Identity within the external source."),
        ),
    ),
    CatalogueObject(
        "view", "link_occurrence", "views/002_link_occurrence.sql",
        (
            "link_occurrence_id", "observation_id", "content_id",
            "element_index", "observed_at", "source_url", "raw_href",
            "target_url", "relation_scope",
        ),
        comment="Observed HTML anchor occurrences resolved in observation context.",
        column_comments=_comments(
            ("link_occurrence_id", "Stable identity of this anchor occurrence."),
            ("observation_id", "Observation in which the anchor was resolved."),
            ("content_id", "Immutable HTML content containing the anchor."),
            ("element_index", "Source element position in document order."),
            ("observed_at", "Time the containing content was observed."),
            ("source_url", "Effective normalized URL containing the anchor."),
            ("raw_href", "Exact href before resolution and normalization."),
            ("target_url", "Normalized resolved HTTP(S) target."),
            ("relation_scope", "Most-specific source-target relationship."),
        ),
        requires_relations=_LINKS,
    ),
    CatalogueObject(
        "view", "object", "views/001_object.sql",
        (
            "content_id", "size_bytes", "detected_media_type",
            "detected_character_encoding", "content_format",
        ),
        schema="content",
        comment="Immutable retained byte sequences keyed by their SHA-256 identity.",
        column_comments=_comments(
            ("content_id", "SHA-256 identity of the logical bytes."),
            ("size_bytes", "Size of the uncompressed logical bytes."),
            ("detected_media_type", "Media type detected by Periplus."),
            ("detected_character_encoding", "Detected character encoding when meaningful."),
            ("content_format", "Detected representation class used for projections."),
        ),
    ),
    CatalogueObject(
        "view", "html_element", "views/002_html_element.sql", _HTML_COLUMNS,
        schema="content",
        comment="Deterministic HTML5 elements keyed by immutable content.",
        column_comments=_HTML_COMMENTS,
        requires_relations=_HTML,
    ),
    *HELPERS,
)
