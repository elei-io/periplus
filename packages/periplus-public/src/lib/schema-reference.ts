// Public catalogue v1.1.0. Keep aligned with public_registry.py and its SQL views.
// DESCRIBE links on /docs expose the deployed contract directly.
export const schemaReference = [
  { name: "web.observation", grain: "One observation of a URL at a point in time, including unsuccessful observations.", key: "observation_id", columns: [
    ["observation_id", "UUID", "Unique observation identity."],
    ["crawl_id", "UUID", "Collection run that produced this observation."],
    ["requested_url", "VARCHAR", "URL Periplus attempted to visit."],
    ["effective_url", "VARCHAR", "Final URL after navigation or redirects; may be null."],
    ["observed_at", "TIMESTAMPTZ", "Capture time, when available; may be null."],
    ["outcome", "VARCHAR", "Final logical observation outcome."],
    ["http_status_code", "INTEGER", "HTTP status, when available."],
    ["content_id", "VARCHAR", "Retained content’s SHA-256 identity; null when no content was retained."],
    ["source_kind", "VARCHAR", "Native Periplus or external source kind."],
    ["source_system", "VARCHAR", "External source system, when applicable."],
    ["source_dataset", "VARCHAR", "External dataset, when applicable."],
    ["source_record_id", "VARCHAR", "Record identity within the external source."],
  ] },
  { name: "content.object", grain: "One distinct retained byte sequence, shared across observations with identical content.", key: "content_id", columns: [
    ["content_id", "VARCHAR", "SHA-256 identity of the logical bytes."],
    ["size_bytes", "BIGINT", "Size of the uncompressed logical bytes."],
    ["detected_media_type", "VARCHAR", "Detected media type."],
    ["detected_character_encoding", "VARCHAR", "Detected character encoding, when meaningful."],
    ["content_format", "VARCHAR", "html, json, pdf, image, xml, text, or binary."],
  ] },
  { name: "content.html_element", grain: "One element in the deterministic HTML5 structure of a retained content object.", key: "content_id + element_index", columns: [
    ["content_id", "VARCHAR", "Identity of the HTML content."],
    ["element_index", "INTEGER", "Zero-based position in depth-first document order."],
    ["parent_index", "INTEGER", "Parent element index; null for the root."],
    ["subtree_end_index", "INTEGER", "Exclusive end of this element’s subtree."],
    ["depth", "INTEGER", "Element depth from the document root."],
    ["child_index", "INTEGER", "Zero-based position among element siblings."],
    ["tag", "VARCHAR", "Normalized local tag name."],
    ["namespace", "VARCHAR", "Normalized element namespace."],
    ["attributes", "MAP(VARCHAR, VARCHAR)", "Attribute names and string values; access with attributes['class']."],
    ["text_direct", "VARCHAR", "Text directly inside the element, before child elements."],
    ["text_tail", "VARCHAR", "Text following this element within its parent."],
  ] },
  { name: "web.link_occurrence", grain: "One observed anchor occurrence, resolved against the URL of its containing observation.", key: "link_occurrence_id", columns: [
    ["link_occurrence_id", "UUID", "Stable identity of the anchor occurrence."],
    ["observation_id", "UUID", "Observation in which this anchor was resolved."],
    ["content_id", "VARCHAR", "Content containing the anchor."],
    ["element_index", "INTEGER", "Anchor element’s position in the HTML structure."],
    ["observed_at", "TIMESTAMPTZ", "Time the containing content was observed."],
    ["source_url", "VARCHAR", "Effective normalized URL containing the anchor."],
    ["raw_href", "VARCHAR", "Original href before resolution and normalization."],
    ["target_url", "VARCHAR", "Resolved, normalized HTTP(S) destination."],
    ["relation_scope", "VARCHAR", "Most-specific relationship: self, same_origin, same_host, same_site, or external."],
  ] },
] as const

export function sqlDraftLink(sql: string) {
  return `/discover?${new URLSearchParams({ mode: "sql", sql })}`
}
