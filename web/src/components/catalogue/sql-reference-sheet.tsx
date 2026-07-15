import { BookOpenIcon, BracesIcon, Table2Icon } from "lucide-react"

import { catalogueTables } from "@/components/catalogue/catalogue-schema"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"

const tableDescriptions: Record<keyof typeof catalogueTables, string> = {
  documents: "Immutable HTML objects and parser provenance, one row per document identity.",
  elements: "Versioned structural DOM projection. element_index ordering and subtree bounds are document-local.",
  crawls: "Acquisition evidence, URL provenance, response metadata, and the document produced by each crawl.",
}

const columnTypes: Record<string, string> = {
  document_id: "VARCHAR",
  html_sha256: "VARCHAR",
  html_object_key: "VARCHAR",
  html_content_type: "VARCHAR",
  html_encoding: "VARCHAR",
  html_size_bytes: "BIGINT",
  html_compressed_size_bytes: "BIGINT",
  compression: "VARCHAR",
  dom_schema_version: "INTEGER",
  parser_name: "VARCHAR",
  parser_version: "VARCHAR",
  parser_options_hash: "VARCHAR",
  element_count: "BIGINT",
  quality_schema_version: "INTEGER",
  html_character_count: "BIGINT",
  visible_text_chars: "BIGINT",
  script_count: "BIGINT",
  app_marker_count: "BIGINT",
  lazy_marker_count: "BIGINT",
  interaction_marker_count: "BIGINT",
  button_count: "BIGINT",
  form_count: "BIGINT",
  input_count: "BIGINT",
  anchor_count: "BIGINT",
  quality_flags_json: "JSON",
  created_at: "TIMESTAMPTZ",
  element_index: "INTEGER",
  parent_index: "INTEGER?",
  subtree_end_index: "INTEGER",
  depth: "INTEGER",
  tag: "VARCHAR",
  namespace_uri: "VARCHAR?",
  attributes: "MAP(VARCHAR, VARCHAR)",
  text_direct: "VARCHAR",
  text_tail: "VARCHAR",
  crawl_id: "UUID",
  graph_id: "UUID",
  graph_run_id: "UUID",
  graph_node_id: "UUID",
  crawl_request_id: "UUID",
  purpose: "VARCHAR",
  trial_id: "UUID?",
  source_crawl_id: "UUID?",
  source_edge_id: "UUID?",
  requested_url: "VARCHAR",
  normalized_url: "VARCHAR",
  final_url: "VARCHAR?",
  page_url: "VARCHAR",
  url_scheme: "VARCHAR",
  url_host: "VARCHAR",
  url_port: "INTEGER",
  url_registrable_domain: "VARCHAR",
  url_path: "VARCHAR",
  url_query: "VARCHAR",
  captured_at: "TIMESTAMPTZ",
  status_code: "INTEGER?",
  duration_ms: "BIGINT?",
  response_media_type: "VARCHAR?",
  response_filename: "VARCHAR?",
  profile: "VARCHAR",
  crawl_profile_id: "UUID?",
  crawl_profile_slug: "VARCHAR",
  remote_concurrency: "INTEGER",
  config_hash: "VARCHAR",
  config_json: "JSON",
  crawl_policy_id: "UUID?",
  outcome: "VARCHAR",
  failure_code: "VARCHAR?",
  failure_stage: "VARCHAR?",
  failure_retryable: "BOOLEAN?",
  failure_detail: "VARCHAR?",
  trial_sampler_version: "INTEGER?",
  trial_sample_rate: "DOUBLE?",
  trial_candidate_strategy: "VARCHAR?",
  trial_candidate_profile_id: "UUID?",
  trial_candidate_profile_slug: "VARCHAR?",
  trial_candidate_profile_config_hash: "VARCHAR?",
}

const helpers = [
  {
    signature: "css_select('article > a[href]')",
    explicit: "css_select(e, 'article > a[href]')",
    description: "Tests the current element with a CSS selector. The alias is inferred when there is one elements source.",
  },
  {
    signature: "get_attribute('href')",
    explicit: "get_attribute(e, 'href')",
    description: "Returns an attribute value or NULL. Names are HTML-case-normalized; Clark notation addresses namespaced attributes.",
  },
  {
    signature: "has_attribute('href')",
    explicit: "has_attribute(e, 'href')",
    description: "Tests attribute presence without conflating a missing attribute with an empty value.",
  },
  {
    signature: "readable_text()",
    explicit: "readable_text(e)",
    description: "Returns normalized human-readable text for the selected element subtree.",
  },
  {
    signature: "text_content()",
    explicit: "text_content(e)",
    description: "Returns DOM textContent for the selected element subtree, including descendant and tail text.",
  },
  {
    signature: "inner_html()",
    explicit: "inner_html(e)",
    description: "Serializes the selected element's children as canonical HTML.",
  },
  {
    signature: "has_text(value)",
    explicit: null,
    description: "True when a value contains at least one non-whitespace character.",
  },
  {
    signature: "resolve_url(source_url, href)",
    explicit: null,
    description: "Resolves a relative URL reference against its source page URL.",
  },
]

const selectorGroups = [
  "Elements: *, article, svg|circle",
  "Identity: #id, .class",
  "Attributes: [href], [rel~=next], [lang|=en], [data-x^=v], [data-x$=v], [data-x*=v]",
  "Relations: A B, A > B, A + B, A ~ B, A, B",
  "Logic: :is(), :where(), :not(), :has()",
  "Structure: :root, :empty, :first-child, :last-child, :only-child, :nth-child(), and of-type variants",
  "Language and links: :lang(), :dir(), :any-link, :scope",
]

function Code({ children }: { children: string }) {
  return <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground">{children}</code>
}

export function SqlReferenceSheet() {
  return (
    <Sheet>
      <SheetTrigger render={<Button size="sm" variant="ghost" />}>
        <BookOpenIcon /> SQL reference
      </SheetTrigger>
      <SheetContent
        side="right"
        overlayClassName="bg-black/35"
        className="w-[min(94vw,48rem)] data-[side=right]:sm:max-w-3xl"
      >
        <SheetHeader className="border-b pr-14">
          <SheetTitle>Catalogue SQL reference</SheetTitle>
          <SheetDescription>
            Atlas tables, DOM helpers, CSS selectors, and their concise single-source forms.
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-8 overflow-y-auto p-6">
          <section className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><BracesIcon className="size-4 text-primary" />DOM helpers</div>
            <p className="text-xs text-muted-foreground">Short forms infer the only visible row source. Use the alias form in joins. The source must expose the required element columns.</p>
            <div className="grid gap-2">
              {helpers.map((helper) => (
                <div key={helper.signature} className="rounded-xl border bg-card/60 p-3">
                  <div className="flex flex-wrap items-center gap-2"><Code>{helper.signature}</Code>{helper.explicit && <><span className="text-muted-foreground">or</span><Code>{helper.explicit}</Code></>}</div>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">{helper.description}</p>
                </div>
              ))}
            </div>
          </section>

          <section className="space-y-3">
            <div className="flex items-center gap-2 text-sm font-semibold"><BracesIcon className="size-4 text-primary" />CSS selectors</div>
            <div className="space-y-2 rounded-xl border bg-card/60 p-3">
              {selectorGroups.map((group) => <div key={group} className="font-mono text-[11px] leading-5 text-muted-foreground">{group}</div>)}
            </div>
            <p className="text-xs leading-5 text-muted-foreground">Unbounded structural selectors run globally and may be expensive. Atlas emits a lint warning but does not block execution.</p>
          </section>

          <section className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-semibold"><Table2Icon className="size-4 text-primary" />Tables</div>
            {(Object.entries(catalogueTables) as Array<[keyof typeof catalogueTables, readonly string[]]>).map(([table, columns]) => (
              <div key={table} className="overflow-hidden rounded-xl border bg-card/60">
                <div className="border-b px-4 py-3"><div className="font-mono text-xs font-semibold">atlas.main.{table}</div><p className="mt-1 text-xs leading-5 text-muted-foreground">{tableDescriptions[table]}</p></div>
                <div className="divide-y">
                  {columns.map((column) => (
                    <div key={column} className="flex items-center justify-between gap-4 px-4 py-2 font-mono text-[11px]"><span>{column}</span><span className="text-right text-muted-foreground">{columnTypes[column] ?? "VARCHAR"}</span></div>
                  ))}
                </div>
              </div>
            ))}
          </section>
        </div>
      </SheetContent>
    </Sheet>
  )
}
