import type { ComponentType, ReactNode } from "react"
import {
  ArrowRightIcon,
  CircleGaugeIcon,
  DatabaseIcon,
  FileArchiveIcon,
  FileCode2Icon,
  FileTextIcon,
  GitForkIcon,
  Globe2Icon,
  HardDriveIcon,
  Layers3Icon,
  RotateCcwIcon,
  ServerCogIcon,
} from "lucide-react"

import catalogueSchemaDiagram from "@/assets/catalogue-schema.svg"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Button } from "@/components/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

type DocsPageProps = {
  onNavigate: (href: string) => void
}

type IconComponent = ComponentType<{ className?: string }>

type TocItem = {
  id: string
  label: string
}

const sqlToc: TocItem[] = [
  { id: "model", label: "Data model" },
  { id: "first-query", label: "Start a query" },
  { id: "schemas", label: "Table reference" },
  { id: "dom", label: "DOM helpers" },
  { id: "query-shape", label: "Query shape" },
]

export function SqlQueriesDocsPage({ onNavigate }: DocsPageProps) {
  return (
    <DocsArticle toc={sqlToc}>
      <DocsHeader
        title="Querying crawl evidence"
        description="Atlas exposes retained crawl history and page structure through a read-only DuckLake catalogue. The important first step is knowing which kind of row you are counting."
        action={
          <Button
            variant="outline"
            onClick={() => onNavigate("/catalogue/workbench")}
          >
            Open SQL workbench <ArrowRightIcon />
          </Button>
        }
      />

      <DocsSection
        id="model"
        title="Understand the data model first"
        lead="A crawl is an observation. A document or artifact is the retained content produced by that observation. HTML documents are projected into elements."
      >
        <CatalogueRelationshipDiagram />

        <p>
          The distinction matters because several crawls can resolve to the same
          document. Count rows in
          <InlineCode>crawls</InlineCode> when visits matter; count distinct
          <InlineCode>document_id</InlineCode> when unique HTML matters.
          Artifact crawls reference retained binary content instead and do not
          have DOM elements.
        </p>

        <ReferenceTable
          headers={["Table", "One row represents", "Join", "Use it for"]}
          rows={[
            [
              <TableName key="crawls">crawls</TableName>,
              "One acquisition observation",
              "crawl_id",
              "URLs, timing, outcomes, graph and policy provenance",
            ],
            [
              <TableName key="documents">documents</TableName>,
              "One unique HTML document",
              "document_id",
              "Content identity, parser provenance and size",
            ],
            [
              <TableName key="elements">elements</TableName>,
              "One projected DOM element",
              "document_id + element_index",
              "Structure, attributes and text extraction",
            ],
            [
              <TableName key="artifacts">artifacts</TableName>,
              "One unique retained binary",
              "artifact_id",
              "File identity, byte size and repository provenance",
            ],
          ]}
        />
      </DocsSection>

      <DocsSection
        id="first-query"
        title="Start from the observation you care about"
        lead="Most catalogue work begins by narrowing crawls by site, path and time. The effective URL is page_url; Atlas also stores its parsed components for efficient filtering."
      >
        <CodeExample
          title="Pages observed in one section during July"
          code={`SELECT page_url, captured_at, duration_ms, outcome
FROM crawls
WHERE url_host = 'docs.example.com'
  AND url_path LIKE '/guides/%'
  AND captured_at >= TIMESTAMPTZ '2026-07-01 00:00:00+00'
  AND captured_at <  TIMESTAMPTZ '2026-08-01 00:00:00+00'
ORDER BY captured_at DESC;`}
        />

        <div className="grid gap-5 sm:grid-cols-3">
          <NumberedNote number="1" title="Choose the grain">
            Decide whether the answer is about crawl attempts, unique content,
            files, or DOM elements.
          </NumberedNote>
          <NumberedNote number="2" title="Bound the scan">
            Filter crawl dates and URL components before joining to content or
            applying DOM helpers.
          </NumberedNote>
          <NumberedNote number="3" title="Join deliberately">
            Join through <InlineCode>document_id</InlineCode> or{" "}
            <InlineCode>artifact_id</InlineCode> only when the question needs
            retained content.
          </NumberedNote>
        </div>

        <Callout title="A common counting mistake">
          Joining <InlineCode>documents</InlineCode> to{" "}
          <InlineCode>crawls</InlineCode> can return the same document more than
          once. That is correct: identical content may have been observed at
          different URLs or times.
        </Callout>
      </DocsSection>

      <DocsSection
        id="schemas"
        title="Table reference"
        lead="Choose a table, then scan its fields by concern. Each view shows the complete public shape without mixing unrelated columns."
      >
        <SchemaReference
          tables={[
            {
              name: "crawls",
              summary:
                "Acquisition, URL, response, graph and policy provenance",
              groups: [
                [
                  "Identity",
                  "crawl_id, document_id, artifact_id, crawl_request_id",
                ],
                [
                  "Graph provenance",
                  "graph_id, graph_run_id, graph_node_id, source_crawl_id, source_edge_id",
                ],
                [
                  "URL",
                  "requested_url, normalized_url, final_url, page_url, url_scheme, url_host, url_port, url_registrable_domain, url_path, url_query",
                ],
                [
                  "Response",
                  "captured_at, status_code, duration_ms, response_media_type, response_filename, outcome",
                ],
                [
                  "Policy",
                  "policy_config_hash, policy_config_json, crawl_policy_id",
                ],
                [
                  "Failure",
                  "failure_code, failure_stage, failure_retryable, failure_detail",
                ],
                ["Attempts", "acquisition_attempts_json"],
              ],
            },
            {
              name: "documents",
              summary:
                "Content identity and parser provenance",
              groups: [
                [
                  "Identity and storage",
                  "document_id, html_sha256, html_object_key, html_content_type, html_encoding, html_size_bytes, html_compressed_size_bytes, compression",
                ],
                [
                  "Parser",
                  "dom_schema_version, parser_name, parser_version, parser_options_hash, element_count",
                ],
                ["Time", "created_at"],
              ],
            },
            {
              name: "elements",
              summary: "Versioned structural DOM projection in document order",
              groups: [
                ["Identity", "document_id, element_index"],
                ["Tree position", "parent_index, subtree_end_index, depth"],
                ["Structure", "tag, namespace_uri, attributes"],
                ["Text", "text_direct, text_tail"],
              ],
            },
            {
              name: "artifacts",
              summary: "Immutable, content-addressed non-HTML files",
              groups: [
                ["Identity", "artifact_id, sha256"],
                ["Storage", "object_key, size_bytes"],
                [
                  "Media type",
                  "response_media_type, detected_media_type, detector_name, detector_version, detection_confidence",
                ],
                ["Time", "created_at"],
              ],
            },
          ]}
        />
      </DocsSection>

      <DocsSection
        id="dom"
        title="Query page structure with DOM helpers"
        lead="Elements are stored in depth-first document order. element_index identifies a row within one document; subtree_end_index marks the inclusive end of its complete subtree."
      >
        <CodeExample
          title="Extract absolute links and readable labels"
          code={`WITH links AS MATERIALIZED (
  SELECT c.page_url, e.document_id, e.element_index, e.attributes
  FROM crawls AS c
  JOIN elements AS e USING (document_id)
  WHERE e.tag = 'a'
    AND macros.has_attribute(e.attributes, 'href')
  LIMIT 1000
)
SELECT
  macros.readable_text(document_id, element_index) AS label,
  macros.resolve_url(
    page_url,
    macros.get_attribute(attributes, 'href')
  ) AS url
FROM links;`}
        />

        <ReferenceTable
          headers={["Helper", "Returns", "Use when"]}
          rows={[
            [
              <InlineCode key="get">
                macros.get_attribute(attributes, name)
              </InlineCode>,
              "Attribute value or NULL",
              "You need the stored href, src, class or data attribute",
            ],
            [
              <InlineCode key="has">
                macros.has_attribute(attributes, name)
              </InlineCode>,
              "Boolean",
              "Presence differs from an empty attribute value",
            ],
            [
              <InlineCode key="selector">
                macros.query_selector_all(selector, document_id)
              </InlineCode>,
              "Element rows",
              "DOM structure is clearer as a query selector",
            ],
            [
              <InlineCode key="read">
                macros.readable_text(document_id, element_index)
              </InlineCode>,
              "Whitespace-normalized subtree text",
              "You want text for reading or search",
            ],
            [
              <InlineCode key="text">
                macros.text_content(document_id, element_index)
              </InlineCode>,
              "DOM textContent semantics",
              "Exact parsed character data matters",
            ],
            [
              <InlineCode key="html">
                macros.inner_html(document_id, element_index)
              </InlineCode>,
              "Canonical child HTML",
              "You need deterministic projected markup, not source bytes",
            ],
            [
              <InlineCode key="resolve">
                macros.resolve_url(source, href)
              </InlineCode>,
              "Absolute URL",
              "A stored link may be relative to its source page",
            ],
          ]}
        />
      </DocsSection>

      <DocsSection
        id="query-shape"
        title="Keep expensive work bounded"
        lead="DOM helpers inspect an element subtree. Select a small, relevant set of elements before applying them across a large catalogue."
      >
        <div className="grid gap-x-10 gap-y-6 sm:grid-cols-2">
          <Rule title="Filter partitioned crawls early">
            <InlineCode>crawls</InlineCode> is partitioned by day of{" "}
            <InlineCode>captured_at</InlineCode>. Include a bounded time range
            whenever the question allows it.
          </Rule>
          <Rule title="Materialize the bounded element set">
            A <InlineCode>MATERIALIZED</InlineCode> CTE makes the intended
            helper input explicit and prevents repeated subtree work.
          </Rule>
          <Rule title="Treat readable text as an approximation">
            It collapses whitespace but does not inspect computed CSS. Hidden,
            script, style, or template content can still appear.
          </Rule>
          <Rule title="Use raw HTML for source fidelity">
            <InlineCode>inner_html</InlineCode> is canonical projected markup.
            Immutable raw HTML remains the source-preserving representation.
          </Rule>
        </div>
      </DocsSection>
    </DocsArticle>
  )
}

const graphToc: TocItem[] = [
  { id: "model", label: "Execution model" },
  { id: "vocabulary", label: "Vocabulary" },
  { id: "first-graph", label: "Build a graph" },
  { id: "edges", label: "Edge SQL" },
  { id: "deduplication", label: "Deduplication" },
  { id: "lifecycle", label: "Lifecycle & safety" },
]

export function CrawlGraphsDocsPage({ onNavigate }: DocsPageProps) {
  return (
    <DocsArticle toc={graphToc}>
      <DocsHeader
        title="From one page to the next"
        description="A crawl graph describes how admitted URLs become page acquisitions and how durable evidence from one page selects the URLs that follow."
        action={
          <Button
            variant="outline"
            onClick={() => onNavigate("/crawls/graphs")}
          >
            Open graph builder <ArrowRightIcon />
          </Button>
        }
      />

      <DocsSection
        id="model"
        title="The execution model"
        lead="Nodes do one thing: turn URL inputs into crawl work. Edges do one thing: run bounded SQL over a completed source crawl and return URL inputs for another node."
      >
        <GraphExecutionDiagram />

        <div className="grid gap-x-10 gap-y-6 sm:grid-cols-2">
          <Rule title="Acquisition owns one page">
            The worker retains immutable HTML or an allowed artifact, publishes
            ingestion work, then publishes navigation readiness. It never waits
            for the catalogue commit.
          </Rule>
          <Rule title="Navigation starts from the current page">
            Branch nodes evaluate their bounded page package immediately with a
            bound <InlineCode>$crawl_id</InlineCode>. Historical joins use the
            run&apos;s pinned catalogue snapshot.
          </Rule>
        </div>
      </DocsSection>

      <DocsSection
        id="vocabulary"
        title="The parts of a graph"
        lead="Definitions live in the Postgres control plane. Current runs and queued work live in NATS. Durable crawl history lives in DuckLake."
      >
        <ReferenceTable
          headers={["Concept", "What it defines", "What it does not own"]}
          rows={[
            [
              <TableName key="graph">Graph</TableName>,
              "Name, description, nodes, edges and one root node",
              "Run state, URL lists or execution limits",
            ],
            [
              <TableName key="node">Node</TableName>,
              "A named acquisition stage that accepts URL inputs",
              "A growing batch of URLs or a special action type",
            ],
            [
              <TableName key="edge">Edge</TableName>,
              "Source, target, bounded SQL and a deduplication mode",
              "Acquisition, writes or arbitrary application code",
            ],
            [
              <TableName key="policy">Crawl Policy</TableName>,
              "Response handling and content-completion capabilities matched by URL",
              "Transport choice, browser capacity or graph topology",
            ],
            [
              <TableName key="domain-policy">Domain Policy</TableName>,
              "Website concurrency and minimum request interval matched by host",
              "CDP browser-fleet capacity or transport configuration",
            ],
            [
              <TableName key="run">Graph Run</TableName>,
              "A frozen executable graph and its current progress",
              "Editable definitions or permanent crawl history",
            ],
            [
              <TableName key="request">Crawl Request</TableName>,
              "One independently claimable URL assigned to one node",
              "A multi-page task or workflow payload",
            ],
          ]}
        />
      </DocsSection>

      <DocsSection
        id="first-graph"
        title="Build a paginated search graph"
        lead="A search crawl usually needs one node for listing pages and one for detail pages. Two edges express both discovery paths."
      >
        <SearchGraphDiagram />

        <Callout title="Root only determines initial admission">
          The root node may also receive URLs from edges and participate in
          cycles. A graph without a root is a valid draft, but it cannot run.
        </Callout>
      </DocsSection>

      <DocsSection
        id="edges"
        title="Write edge SQL for one source crawl"
        lead="An edge runs once for each ready source crawl. It should answer a local question about that page, not aggregate the unbounded history of the node."
      >
        <CodeExample
          title="Select external result links"
          code={`SELECT target_url AS url
FROM edge.page_links
WHERE crawl_id = $crawl_id
  AND relation_kind = 'external'
ORDER BY element_index
LIMIT 10;`}
        />

        <div className="grid gap-x-10 gap-y-6 sm:grid-cols-2">
          <Rule title="Return a URL column">
            Each returned URL becomes a candidate input for the edge’s target
            node. Rows do not become a general workflow payload.
          </Rule>
          <Rule title="Scope with $crawl_id">
            Edge evaluation is page-local and repeatable. The bound crawl has
            already passed navigation readiness.
          </Rule>
          <Rule title="Use LIMIT to express cardinality">
            SQL limits describe how many candidates the edge intends to select
            before runtime deduplication.
          </Rule>
          <Rule title="Leave acquisition to the target request">
            URL matching chooses and freezes the effective Crawl Policy and
            Domain Policy after the edge emits a candidate.
          </Rule>
        </div>
      </DocsSection>

      <DocsSection
        id="deduplication"
        title="Choose a deduplication scope"
        lead="Normalization and deduplication happen after SQL selection. The default graph scope is right for most traversal."
      >
        <ReferenceTable
          headers={["Mode", "Suppresses a URL already admitted…", "Use when"]}
          rows={[
            [
              <InlineCode key="graph">graph</InlineCode>,
              "Anywhere in the current graph run",
              "A URL should normally be fetched once per run",
            ],
            [
              <InlineCode key="crawl">crawl</InlineCode>,
              "By this edge for the same source crawl",
              "The same target URL may be meaningful when emitted by different source pages",
            ],
            [
              <InlineCode key="document">document</InlineCode>,
              "By this edge for crawls sharing one source document",
              "Repeated observations of identical HTML should share emitted targets",
            ],
          ]}
        />
        <p className="text-sm text-muted-foreground">
          SQL <InlineCode>LIMIT</InlineCode> is applied before deduplication, so
          the number of newly admitted requests can be smaller than the number
          of rows selected.
        </p>
      </DocsSection>

      <DocsSection
        id="lifecycle"
        title="Editing, execution and safety"
        lead="A run freezes the complete executable graph. Later edits cannot change work that is already active."
      >
        <div className="divide-y overflow-hidden rounded-lg border bg-card/25">
          <LifecycleRow title="Definitions become immutable after use">
            An unused node or edge can be edited. Once it has participated in a
            run, changing it means deleting it and creating a replacement.
          </LifecycleRow>
          <LifecycleRow title="Every URL is independent work">
            One logical crawl request contains one URL. Claiming, retries,
            failure isolation and provenance remain per request even when one
            edge emits many URLs.
          </LifecycleRow>
          <LifecycleRow title="Cycles are valid">
            Self-edges and directed cycles support pagination and recursive
            discovery. Graph-wide deduplication breaks exact loops by default.
          </LifecycleRow>
          <LifecycleRow title="Bounds remain explicit">
            Edge limits express intent. Catalogue row, byte, memory and timeout
            ceilings bound SQL. Deployment run ceilings stop malformed
            unique-URL expansion.
          </LifecycleRow>
        </div>
      </DocsSection>
    </DocsArticle>
  )
}

const resourcesToc: TocItem[] = [
  { id: "model", label: "Scaling model" },
  { id: "clients", label: "Client pools" },
  { id: "diagnosis", label: "Diagnose pressure" },
  { id: "rules", label: "Operating rules" },
]

export function ResourcesScalingDocsPage({ onNavigate }: DocsPageProps) {
  return (
    <DocsArticle toc={resourcesToc}>
      <DocsHeader
        title="Scale the limiting resource"
        description="Atlas starts with one ingestion process with four managed Quack clients and one materialization process with eight; Basin owns remote compute admission and scaling."
        action={
          <Button
            variant="outline"
            onClick={() => onNavigate("/crawls/metrics")}
          >
            Open crawl metrics <ArrowRightIcon />
          </Button>
        }
      />

      <DocsSection
        id="model"
        title="Scale clients before processes"
        lead="A session-affine client is the unit of catalogue concurrency. Horizontal replicas remain available for resilience and post-saturation growth."
      >
        <ScalingModelDiagram />

        <ReferenceTable
          headers={["Mechanism", "Question it answers", "Changed by"]}
          rows={[
            [
              <TableName key="clients">Quack client lanes</TableName>,
              "How many independent catalogue operations can this process submit?",
              "Code-owned pool size; four for ingestion and eight for materialization",
            ],
            [
              <TableName key="replicas">Worker replicas</TableName>,
              "Does this role need another failure boundary or more capacity?",
              "Deployment scaling after the local client pool is saturated",
            ],
            [
              <TableName key="basin">Basin capacity</TableName>,
              "How much remote DuckLake work can run?",
              "DuckBasin Quack autoscaling and provider-side admission",
            ],
            [
              <TableName key="domains">Domain limits</TableName>,
              "May another request reach this website now?",
              "Per-domain policy, independently coordinated across replicas",
            ],
          ]}
        />
      </DocsSection>

      <DocsSection
        id="clients"
        title="One connection remains one lane"
        lead="The pool adds concurrency without allowing concurrent use of a session-affine DuckDB connection."
      >
        <ReferenceTable
          headers={["Role", "Default", "Ownership"]}
          rows={[
            [
              <TableName key="ingestion">Ingestion</TableName>,
              "1 process × 4 clients",
              "Each lane pulls, prepares and commits its own ingestion batches",
            ],
            [
              <TableName key="materialization">Materialization</TableName>,
              "1 process × 8 clients",
              "Stable materialization-ID sharding assigns definitions to lanes",
            ],
            [
              <TableName key="api">Interactive API</TableName>,
              "Bounded configured pool",
              "A query borrows one client until its Arrow stream closes",
            ],
          ]}
        />

        <p>
          Operation leases suppress duplicate durable execution. Ingestion
          does not hold PostgreSQL advisory locks across remote DuckLake
          commits.
        </p>
      </DocsSection>

      <DocsSection
        id="diagnosis"
        title="Diagnose before changing capacity"
        lead="Queue age, active client lanes, transfer latency and Basin metrics identify the limiting layer."
      >
        <ReferenceTable
          headers={["Observed signal", "Likely constraint", "Next action"]}
          rows={[
            [
              "Acquisition slots stay full while the crawl queue grows",
              "Acquisition workers",
              "Add acquisition replicas; domain policies continue to enforce website politeness",
            ],
            [
              "Ingestion queue age rises while fewer than four lanes are active",
              "Local preparation or a stuck client",
              "Inspect staging, raw-object latency and the affected client session",
            ],
            [
              "All four lanes remain active and Basin scales Quack",
              "Managed DuckLake capacity",
              "Inspect Basin and Quack latency before adding another Atlas process",
            ],
            [
              "Object latency rises while catalogue lanes idle",
              "Object-store throughput",
              "Inspect S3 networking and the owning process-local client pool",
            ],
            [
              "Materialized views lag while graph ingestion is healthy",
              "Materialization lanes or query cost",
              "Inspect per-definition work, then add a replica only after all lanes saturate",
            ],
          ]}
        />

        <Callout title="Basin is the managed capacity boundary">
          Atlas deliberately has no deployment-wide catalogue or object-store
          admission layer. Client pools bound submitted work; Basin decides
          how remote analytical capacity is scheduled and scaled.
        </Callout>
      </DocsSection>

      <DocsSection
        id="rules"
        title="Operating rules"
        lead="These rules keep scaling changes from becoming correctness changes."
      >
        <div className="divide-y overflow-hidden rounded-lg border bg-card/25">
          <LifecycleRow title="Keep browser capacity and website politeness separate">
            Scale acquisition coordination and the CDP browser fleet independently.
            Domain policies remain Atlas-owned correctness constraints.
          </LifecycleRow>
          <LifecycleRow title="Keep graph-critical work independent of view freshness">
            Slow ingestion or materialization may make catalogue state stale,
            but neither may hold browser capacity or keep a graph run active.
          </LifecycleRow>
          <LifecycleRow title="Keep distributed coordination narrow">
            Website concurrency is keyed per hostname. Do not put unrelated
            domains, catalogue clients, queries and object transfers behind one
            global state record.
          </LifecycleRow>
          <LifecycleRow title="Measure before adding replicas">
            Compare active client lanes, queue age, transfer latency and Basin
            scaling decisions. A second process is useful after the first
            process&apos;s four ingestion lanes are consistently busy.
          </LifecycleRow>
        </div>
      </DocsSection>
    </DocsArticle>
  )
}

function DocsArticle({
  toc,
  children,
}: {
  toc: TocItem[]
  children: ReactNode
}) {
  return (
    <div className="mx-auto grid w-full max-w-6xl gap-12 pb-16 xl:grid-cols-[minmax(0,1fr)_9rem]">
      <article className="max-w-4xl min-w-0">{children}</article>
      <aside className="hidden xl:block">
        <nav aria-label="On this page" className="sticky top-2 space-y-1">
          <p className="mb-3 text-[10px] font-semibold tracking-[0.16em] text-muted-foreground uppercase">
            On this page
          </p>
          {toc.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className="block border-l pl-3 text-xs leading-7 text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
            >
              {item.label}
            </a>
          ))}
        </nav>
      </aside>
    </div>
  )
}

function DocsHeader({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action: ReactNode
}) {
  return (
    <header className="border-b pt-2 pb-10">
      <h1 className="max-w-3xl text-3xl font-semibold tracking-[-0.025em] sm:text-4xl">
        {title}
      </h1>
      <p className="mt-4 max-w-3xl text-base leading-7 text-muted-foreground">
        {description}
      </p>
      <div className="mt-6">{action}</div>
    </header>
  )
}

function DocsSection({
  id,
  title,
  lead,
  children,
}: {
  id: string
  title: string
  lead?: string
  children: ReactNode
}) {
  return (
    <section id={id} className="scroll-mt-6 border-b py-10 last:border-b-0">
      <h2 className="text-xl font-semibold tracking-[-0.015em] sm:text-2xl">
        {title}
      </h2>
      {lead && (
        <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">
          {lead}
        </p>
      )}
      <div className="mt-7 space-y-7 text-sm leading-6 text-foreground/90">
        {children}
      </div>
    </section>
  )
}

function CatalogueRelationshipDiagram() {
  return (
    <figure>
      <div className="overflow-hidden rounded-lg border bg-card/15 p-2 sm:p-3">
        <img
          src={catalogueSchemaDiagram}
          alt="Entity relationship diagram connecting crawls to documents, elements and artifacts through their join columns"
          className="block w-full"
        />
      </div>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Relationships connect the actual join columns. Nullable document and
        artifact references are mutually exclusive for successful crawls; a
        failed acquisition has neither.
      </figcaption>
    </figure>
  )
}

function GraphExecutionDiagram() {
  const items = [
    [Globe2Icon, "URL candidate", "offered to the graph"],
    [GitForkIcon, "Node admission", "one crawl request"],
    [ServerCogIcon, "Acquire", "one remote page"],
    [DatabaseIcon, "Navigate", "ingestion accepted"],
  ] as const

  return (
    <figure>
      <div className="overflow-hidden rounded-lg border bg-card/15">
        <div className="p-4 sm:p-6">
          <p className="mb-4 text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
            One crawl request
          </p>
          <div className="grid gap-2 sm:grid-cols-4 sm:items-start">
            {items.map(([Icon, name, detail], index) => (
              <div
                key={name}
                className="relative flex gap-3 py-1 sm:block sm:text-center"
              >
                <div className="flex size-8 shrink-0 items-center justify-center rounded-full border bg-background sm:mx-auto">
                  <Icon className="size-3.5 text-muted-foreground" />
                </div>
                <div className="sm:mt-2">
                  <p className="text-xs font-medium">{name}</p>
                  <p className="mt-0.5 text-[10px] leading-4 text-muted-foreground">
                    {detail}
                  </p>
                </div>
                {index < items.length - 1 && (
                  <ArrowRightIcon className="absolute top-4 -right-2 hidden size-3 text-muted-foreground/60 sm:block" />
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="grid border-t sm:grid-cols-2 sm:divide-x">
          <div className="p-4 sm:p-5">
            <p className="text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
              HTML path
            </p>
            <div className="mt-3 flex items-center gap-3">
              <FlowToken icon={FileCode2Icon} label="Edge SQL" />
              <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
              <FlowToken icon={Globe2Icon} label="URL candidates" />
            </div>
            <div className="mt-3 flex items-center gap-2 text-[11px] font-medium text-primary">
              <RotateCcwIcon className="size-3.5" />
              Each candidate returns to node admission
            </div>
          </div>

          <div className="p-4 sm:p-5">
            <p className="text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
              Artifact path
            </p>
            <div className="mt-3 flex items-center gap-3">
              <FlowToken icon={FileArchiveIcon} label="Retained file" />
              <ArrowRightIcon className="size-3.5 shrink-0 text-muted-foreground" />
              <span className="text-xs font-medium">Complete</span>
            </div>
            <p className="mt-3 text-[11px] leading-4.5 text-muted-foreground">
              No DOM projection and no outgoing edge work.
            </p>
          </div>
        </div>
      </div>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Edge SQL selects candidates; ordinary admission still applies URL
        normalization, deduplication and policy matching.
      </figcaption>
    </figure>
  )
}

function FlowToken({
  icon: Icon,
  label,
}: {
  icon: IconComponent
  label: string
}) {
  return (
    <span className="inline-flex items-center gap-2 text-xs font-medium">
      <Icon className="size-3.5 text-muted-foreground" />
      {label}
    </span>
  )
}

function SearchGraphDiagram() {
  return (
    <figure>
      <div className="overflow-hidden rounded-lg border bg-card/15">
        <div className="p-4 sm:p-6">
          <p className="mb-4 text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
            Fixed graph definition
          </p>
          <div className="grid gap-4 sm:grid-cols-[1fr_10rem_1fr] sm:items-center">
            <div>
              <DiagramEntity
                icon={Globe2Icon}
                name="search_page"
                detail="root · listing pages"
              />
              <div className="mx-3 mt-2 flex items-center gap-2 rounded-b-md border-x border-b border-primary/35 px-3 py-2 text-[10px] text-primary">
                <RotateCcwIcon className="size-3.5 shrink-0" />
                <span>
                  <strong className="font-medium">next-page SQL</strong> returns
                  to this node
                </span>
              </div>
            </div>

            <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
              <span className="h-px flex-1 bg-border" />
              <span>result-link SQL</span>
              <ArrowRightIcon className="size-3.5 shrink-0" />
            </div>

            <DiagramEntity
              icon={FileTextIcon}
              name="result_page"
              detail="detail pages · terminal here"
            />
          </div>
        </div>

        <div className="border-t bg-background/10 p-4 sm:p-6">
          <div className="mb-4 flex items-baseline justify-between gap-4">
            <p className="text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
              One example run
            </p>
            <p className="hidden text-[10px] text-muted-foreground sm:block">
              the definition above does not change
            </p>
          </div>
          <div className="space-y-3">
            <RunExpansionRow
              source="Search 1"
              results={["Result A", "Result B"]}
              next="Search 2"
            />
            <RunExpansionRow
              source="Search 2"
              results={["Result C", "Result D"]}
              next="Search 3"
            />
          </div>
          <p className="mt-4 text-[11px] text-muted-foreground">
            Pagination stops when next-page SQL returns no URL.
          </p>
        </div>
      </div>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Every completed search page evaluates both outgoing edges independently;
        runtime requests expand while the two-node graph stays fixed.
      </figcaption>
    </figure>
  )
}

function RunExpansionRow({
  source,
  results,
  next,
}: {
  source: string
  results: string[]
  next: string
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-[6rem_auto_1fr] sm:items-center">
      <span className="font-mono text-xs font-semibold text-foreground">
        {source}
      </span>
      <ArrowRightIcon className="hidden size-3.5 text-muted-foreground sm:block" />
      <div className="flex flex-wrap gap-2">
        {results.map((result) => (
          <span
            key={result}
            className="rounded border bg-card/40 px-2 py-1 text-[10px] text-foreground/78"
          >
            {result}
          </span>
        ))}
        <span className="rounded border border-primary/35 bg-primary/5 px-2 py-1 text-[10px] text-primary">
          {next} ↻
        </span>
      </div>
    </div>
  )
}

function ScalingModelDiagram() {
  return (
    <figure>
      <div className="rounded-lg border bg-muted/10 p-5 sm:p-6">
        <div className="grid gap-3 sm:grid-cols-3">
          <DiagramEntity
            icon={Globe2Icon}
            name="Acquisition workers"
            detail="HTTP · browser · provider"
          />
          <DiagramEntity
            icon={DatabaseIcon}
            name="Ingestion process"
            detail="8 session-affine clients"
          />
          <DiagramEntity
            icon={Layers3Icon}
            name="Materialization process"
            detail="8 session-affine clients"
          />
        </div>
        <div className="my-4 flex items-center gap-3 text-[10px] tracking-wider text-muted-foreground uppercase">
          <span className="h-px flex-1 bg-border" /> bounded work reaches its
          owning system <span className="h-px flex-1 bg-border" />
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <DiagramEntity
            icon={CircleGaugeIcon}
            name="Domain politeness"
            detail="one NATS key per hostname"
            subdued
          />
          <DiagramEntity
            icon={DatabaseIcon}
            name="DuckBasin"
            detail="remote admission · autoscaling"
            subdued
          />
          <DiagramEntity
            icon={HardDriveIcon}
            name="Raw object store"
            detail="process-local bounded clients"
            subdued
          />
        </div>
      </div>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        Local pools bound submitted work. Distributed coordination exists only
        for website politeness and exact-operation correctness.
      </figcaption>
    </figure>
  )
}

function DiagramEntity({
  icon: Icon,
  name,
  detail,
  className,
  subdued = false,
}: {
  icon: IconComponent
  name: string
  detail: string
  className?: string
  subdued?: boolean
}) {
  return (
    <div
      className={cn(
        "rounded-md border bg-card px-4 py-3 shadow-sm",
        subdued && "border-dashed bg-transparent shadow-none",
        className
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className="size-3.5 text-muted-foreground" />
        <span className="font-mono text-xs font-medium">{name}</span>
      </div>
      <p className="mt-1 pl-5.5 text-[10px] text-muted-foreground">{detail}</p>
    </div>
  )
}

function ReferenceTable({
  headers,
  rows,
}: {
  headers: string[]
  rows: ReactNode[][]
}) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card/25">
      <Table className="min-w-[42rem]">
        <TableHeader className="bg-muted/15">
          <TableRow className="hover:bg-transparent">
            {headers.map((header) => (
              <TableHead
                key={header}
                className="h-10 px-4 text-[10px] font-semibold tracking-[0.12em] text-foreground/60 uppercase"
              >
                {header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, rowIndex) => (
            <TableRow
              key={rowIndex}
              className="align-top even:bg-muted/5 hover:bg-transparent"
            >
              {row.map((cell, cellIndex) => (
                <TableCell
                  key={cellIndex}
                  className={cn(
                    "px-4 py-3.5 text-[13px] leading-5.5 whitespace-normal",
                    cellIndex > 0 && "text-foreground/76"
                  )}
                >
                  {cell}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function SchemaReference({
  tables,
}: {
  tables: {
    name: string
    summary: string
    groups: [string, string][]
  }[]
}) {
  return (
    <Tabs
      defaultValue={tables[0]?.name}
      className="overflow-hidden rounded-lg border bg-card/35"
    >
      <div className="overflow-x-auto bg-muted/8">
        <TabsList className="px-2">
          {tables.map(({ name }) => (
            <TabsTrigger key={name} value={name}>
              {name}
            </TabsTrigger>
          ))}
        </TabsList>
      </div>
      {tables.map(({ name, summary, groups }) => (
        <TabsContent key={name} value={name}>
          <p className="border-b px-5 py-4 text-[13px] leading-5 text-foreground/78">
            {summary}
          </p>
          <dl className="divide-y">
            {groups.map(([label, columns]) => (
              <div
                key={label}
                className="grid gap-1 px-5 py-4 even:bg-muted/4 sm:grid-cols-[10rem_1fr] sm:gap-6"
              >
                <dt className="text-[10px] font-semibold tracking-[0.1em] text-foreground/72 uppercase">
                  {label}
                </dt>
                <dd className="font-mono text-[13px] leading-6 text-foreground/92">
                  {columns}
                </dd>
              </div>
            ))}
          </dl>
        </TabsContent>
      ))}
    </Tabs>
  )
}

function CodeExample({ title, code }: { title: string; code: string }) {
  const height = Math.max(112, Math.min(380, code.split("\n").length * 23 + 56))

  return (
    <figure className="overflow-hidden rounded-lg border bg-card/50">
      <figcaption className="border-b bg-muted/20 px-4 py-2.5 text-xs font-medium">
        {title}
      </figcaption>
      <SqlEditor
        value={code}
        readOnly
        height={`${height}px`}
        ariaLabel={`Read-only SQL example: ${title}`}
      />
    </figure>
  )
}

function Callout({ title, children }: { title: string; children: ReactNode }) {
  return (
    <aside className="border-l-2 border-primary/50 pl-4">
      <p className="text-xs font-semibold text-foreground">{title}</p>
      <div className="mt-1 text-xs leading-5 text-muted-foreground">
        {children}
      </div>
    </aside>
  )
}

function NumberedNote({
  number,
  title,
  children,
}: {
  number: string
  title: string
  children: ReactNode
}) {
  return (
    <div>
      <span className="font-mono text-[10px] text-muted-foreground">
        {number}
      </span>
      <h3 className="mt-1 text-sm font-medium">{title}</h3>
      <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
        {children}
      </p>
    </div>
  )
}

function Rule({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-medium">{title}</h3>
      <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
        {children}
      </p>
    </div>
  )
}

function LifecycleRow({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <div className="grid gap-2 px-4 py-4 even:bg-muted/5 sm:grid-cols-[13rem_1fr]">
      <h3 className="text-[13px] font-medium text-foreground">{title}</h3>
      <p className="text-[13px] leading-5.5 text-foreground/76">{children}</p>
    </div>
  )
}

function InlineCode({ children }: { children: ReactNode }) {
  return (
    <code className="mx-0.5 rounded bg-muted px-1.5 py-0.5 font-mono text-[0.78em] text-foreground">
      {children}
    </code>
  )
}

function TableName({ children }: { children: ReactNode }) {
  return (
    <code className="font-mono text-xs font-semibold text-foreground">
      {children}
    </code>
  )
}
