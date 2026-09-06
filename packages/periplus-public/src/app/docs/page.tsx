import type { Metadata } from "next"
import Link from "next/link"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Table, TableHeader, TableHead, TableBody, TableRow, TableCell } from "@/components/ui/table"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { SqlEditor } from "@/components/sql-editor"
import { schemaReference, sqlDraftLink } from "@/lib/schema-reference"

export const metadata: Metadata = { title: "SQL documentation", description: "Query the web as structured data. Explore Periplus’s public schema, join keys, SQL examples, and query limits." }

const quickStart = `SELECT requested_url, observed_at, outcome, content_id
FROM web.observation
ORDER BY observed_at DESC NULLS LAST, observation_id
LIMIT 20;`
const headings = `WITH pages AS (
  SELECT observation_id, requested_url, content_id
  FROM web.observation
  WHERE content_id IS NOT NULL
  ORDER BY observed_at DESC NULLS LAST, observation_id
  LIMIT 10
)
SELECT p.requested_url, h.element_index,
       h.text_direct AS heading, h.attributes['class'] AS css_class
FROM pages p
JOIN content.html_element h USING (content_id)
WHERE h.tag = 'h1'
ORDER BY p.observation_id, h.element_index
LIMIT 100;`
const links = `WITH pages AS (
  SELECT observation_id
  FROM web.observation
  WHERE content_id IS NOT NULL
  ORDER BY observed_at DESC NULLS LAST, observation_id
  LIMIT 10
)
SELECT l.source_url, l.target_url, l.relation_scope
FROM pages p
JOIN web.link_occurrence l USING (observation_id)
ORDER BY l.source_url, l.element_index
LIMIT 100;`
const latest = `SELECT requested_url, observed_at, outcome, content_id
FROM web.observation
QUALIFY row_number() OVER (
  PARTITION BY requested_url
  ORDER BY observed_at DESC NULLS LAST, observation_id DESC
) = 1
ORDER BY requested_url
LIMIT 20;`

function Example({ title, description, sql }: { title: string; description: string; sql: string }) {
  return <Card><CardHeader><CardTitle><h3>{title}</h3></CardTitle><CardDescription>{description}</CardDescription></CardHeader><CardContent className="flex min-w-0 flex-col gap-4"><SqlEditor value={sql} readOnly /><Link className="story-link self-start" href={sqlDraftLink(sql)}>Open in SQL bench →</Link></CardContent></Card>
}

export default function Page() {
  return <main className="about-page">
    <header className="about-hero"><span className="eyebrow">Docs / Public SQL contract · v1.0.0</span><h1>The web, in tables.</h1><p>Four views describe what was observed, the content retained, its HTML structure, and the links between pages. Your SQL decides what that structure means.</p></header>
    <nav aria-label="Documentation sections" className="flex flex-wrap gap-5 pb-8">{[["quick-start", "Quick start"], ["schema", "Schema"], ["joins", "Join keys"], ["examples", "Patterns"], ["macros", "Functions & macros"], ["limits", "Query behavior"]].map(([id, label]) => <Link key={id} href={`#${id}`} className="story-link">{label}</Link>)}</nav>
    <section id="quick-start" className="about-section"><span className="eyebrow">01 / Start here</span><div className="flex min-w-0 flex-col gap-5"><h2>Meet the observations.</h2><p>Run this against the current corpus. Each row is an observation, so the same URL can appear more than once. Examples open as editable drafts and run when you choose.</p><Example title="Recent observations" description="Up to 20 observations, with dated captures first. Missing content and unsuccessful observations remain visible." sql={quickStart} /><p>Use two-part names such as <code>web.observation</code> in the SQL bench. The fully qualified form is <code>periplus.web.observation</code>. <Link href="/coverage">Coverage</Link> shows which sites are represented today.</p></div></section>
    <section id="schema" className="about-section"><span className="eyebrow">02 / Schema reference</span><div className="flex min-w-0 flex-col gap-6"><h2>Know what a row means.</h2><p>These are public views. Their keys describe logical identity; they are not declarations of database-enforced primary keys on the views. Use DESCRIBE to inspect deployed column types and nullability.</p>{schemaReference.map(relation => <Card key={relation.name} id={relation.name.replaceAll('.', '-')}><CardHeader><CardTitle><h3 className="break-all"><code>{relation.name}</code></h3></CardTitle><CardDescription>{relation.grain}</CardDescription><CardDescription>Logical key: <code>{relation.key}</code></CardDescription></CardHeader><CardContent className="flex min-w-0 flex-col gap-4"><Table><TableHeader><TableRow><TableHead>Column / type</TableHead><TableHead>Meaning</TableHead></TableRow></TableHeader><TableBody>{relation.columns.map(([name, type, meaning]) => <TableRow key={name}><TableCell className="align-top whitespace-normal"><code className="break-all">{name}</code><CardDescription>{type}</CardDescription></TableCell><TableCell className="whitespace-normal">{meaning}</TableCell></TableRow>)}</TableBody></Table><Link className="story-link" href={sqlDraftLink(`DESCRIBE ${relation.name};`)}>Inspect deployed schema →</Link></CardContent></Card>)}</div></section>
    <section id="joins" className="about-section"><span className="eyebrow">03 / Identity & joins</span><div className="flex flex-col gap-5"><h2>A page visit is not a content object.</h2><p>Two URLs—or two visits to the same URL—can retain identical bytes. Those observations share a <code>content_id</code>. HTML structure is stored once for that content; joining it to observations repeats it for each matching observation.</p><Table><TableHeader><TableRow><TableHead>From → to</TableHead><TableHead>Join on</TableHead></TableRow></TableHeader><TableBody><TableRow><TableCell>Observation → content object</TableCell><TableCell><code>content_id</code></TableCell></TableRow><TableRow><TableCell>Observation → HTML elements</TableCell><TableCell><code>content_id</code></TableCell></TableRow><TableRow><TableCell>Observation → link occurrences</TableCell><TableCell><code>observation_id</code></TableCell></TableRow><TableRow><TableCell>Link occurrence → HTML element</TableCell><TableCell><code>content_id + element_index</code></TableCell></TableRow></TableBody></Table><p>Use a LEFT JOIN to retain observations without matching content or structure. An INNER JOIN keeps only matches. Counting joined HTML rows counts elements, not pages; choose distinct URLs, observation IDs, or content IDs according to your question.</p><Alert><AlertDescription>Link resolution belongs to an observation. Join links on observation_id to preserve the source URL context. Joining only on content_id can mix links from different observations of identical HTML.</AlertDescription></Alert><p>For a parent element, match both <code>content_id</code> and <code>child.parent_index = parent.element_index</code>. A subtree occupies element indices from <code>element_index</code> inclusive to <code>subtree_end_index</code> exclusive.</p></div></section>
    <section id="examples" className="about-section"><span className="eyebrow">04 / Common patterns</span><div className="flex min-w-0 flex-col gap-6"><h2>Build your own view.</h2><Example title="Extract headings and attributes" description="Select ten observations before expanding their HTML. Direct text excludes descendant text; an h1 may contain additional text in child elements. Non-HTML content has no HTML element rows." sql={headings} /><Example title="Explore outgoing links" description="Link occurrences preserve repeated anchors. A destination appearing multiple times is not automatically a duplicate error." sql={links} /><Example title="Choose one observation per URL" description="Chooses the latest dated observation, with a stable ID tie-breaker. It does not imply current live coverage; undated observations sort last. To choose the latest retained content instead, add WHERE content_id IS NOT NULL before QUALIFY." sql={latest} /><p>Explore <Link href="/datasets">named dataset queries</Link> for more complete analyses you can adapt.</p></div></section>
    <section id="macros" className="about-section"><span className="eyebrow">05 / Functions & macros</span><div><h2>SQL supplies the interpretation.</h2><p>The current public contract exposes no Periplus-specific macros. Use DuckDB SQL expressions and built-in functions such as <code>split_part</code>, <code>regexp_extract</code>, <code>try_cast</code>, and window functions. Access HTML attributes with <code>{"attributes['href']"}</code> or <code>{"attributes['class']"}</code>.</p><p>There is no custom query extension to install. The public service restricts queries to the public catalogue and does not permit installing extensions, reading arbitrary files, or changing database state.</p></div></section>
    <section id="limits" className="about-section"><span className="eyebrow">06 / Query behavior</span><div className="flex flex-col gap-5"><h2>Understand the result.</h2><p>The browser sends SQL to a read-only Python query service. Preparation validates and explains the query; execution independently prepares it and returns rows. SQL currently stays unchanged, with DuckDB optimizing the execution plan.</p><Table><TableHeader><TableRow><TableHead>Public preview</TableHead><TableHead>Behavior</TableHead></TableRow></TableHeader><TableBody>{[["SQL", "One read-only statement over the public views. DESCRIBE, EXPLAIN, EXPLAIN ANALYZE, and SUMMARIZE use the same public scope checks."], ["Execution", "20-second interrupt deadline; 512 MB memory and 256 MB spill budget per query process."], ["Results", "Up to 1,000 rows and an 8 MiB result budget. Truncation is reported; CSV exports only the returned rows."], ["Busy service", "One operation per query process. Excess requests receive 429 with Retry-After; there is currently no queue."], ["Parameters", "Use ? placeholders and the SQL bench’s JSON array of positional values."], ["Numbers", "Decimals and integers outside JavaScript’s safe range arrive as strings, alongside SQL type information."], ["Privacy", "SQL and parameters are logged. Shared query links include both. Do not include secrets."]].map(([name, value]) => <TableRow key={name}><TableCell className="align-top">{name}</TableCell><TableCell className="whitespace-normal">{value}</TableCell></TableRow>)}</TableBody></Table><p>A LIMIT bounds output, not necessarily work. Sorting, aggregation, and joins may still scan substantial data. Filter observations and select the columns you need before expanding HTML. EXPLAIN ANALYZE executes the query and consumes its execution budget.</p><p>Missing content can reflect failure, an empty response, or a decision not to retain it. HTML projections may become available after collection finishes. Observation times describe captured data, not a guarantee of freshness; live website links can show different content.</p></div></section>
  </main>
}
