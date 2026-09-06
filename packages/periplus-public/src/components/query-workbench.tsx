"use client"

import { useEffect, useRef, useState } from "react"
import { Download, Play, Share2 } from "lucide-react"
import { toast } from "sonner"
import dynamic from "next/dynamic"

import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Separator } from "@/components/ui/separator"
import { QueryTable } from "@/components/query-table"
import { useQueryExecution } from "@/hooks/use-query-execution"
import Link from "next/link"
import { datasets } from "@/lib/datasets"
import { consumeDiscoveryLaunch } from "@/lib/discovery-launch"
import { extractApiError } from "@/lib/api"

const SqlEditor = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor), { ssr: false, loading: () => <div className="sql-loading">Loading SQL editor…</div> })

const examples = [datasets[0], datasets[1], datasets[3]].map(dataset => ({ label: dataset.name, description: dataset.scope, sql: dataset.sql }))
const relations = [
  { name: "web.observation", grain: "One row per page observation", detail: "Source URLs, available collection dates, outcomes, and content_id. Repeated observations are separate." },
  { name: "content.html_element", grain: "One row per HTML element", detail: "Tags, attributes, text, and document structure. Join on content_id." },
  { name: "web.link_occurrence", grain: "One row per observed link", detail: "Source and target URLs, link scope, and available observation time. Join observations on observation_id." },
  { name: "content.object", grain: "One row per unique content object", detail: "Content format, media type, and byte size." },
]

export function QueryWorkbench({ initialSql, initialParameters, autoRun = false }: { initialSql?: string; initialParameters?: string; autoRun?: boolean }) {
  const [sql, setSql] = useState(initialSql ?? examples[0].sql)
  const [parameters, setParameters] = useState(initialParameters ?? "[]")
  const editor = useRef<HTMLDivElement>(null)
  const query = useQueryExecution()
  const { mutate } = query
  useEffect(() => {
    if (!autoRun || !initialSql?.trim() || !consumeDiscoveryLaunch()) return
    try {
      const values: unknown = JSON.parse(initialParameters ?? "[]")
      if (!Array.isArray(values)) throw new Error("Parameters must be a JSON array.")
      mutate({ sql: initialSql, parameters: values })
    } catch (error) { toast.error(extractApiError(error)) }
  }, [autoRun, initialSql, initialParameters, mutate])
  const activeExample = examples.find(example => example.sql === sql)
  function loadSql(value: string) {
    setSql(value); setParameters("[]")
    editor.current?.querySelector<HTMLElement>('[contenteditable="true"]')?.focus()
    document.getElementById("explore")?.scrollIntoView({ block: "start" })
  }
  function run() {
    try {
      const values: unknown = JSON.parse(parameters)
      if (!Array.isArray(values)) throw new Error("Parameters must be a JSON array.")
      query.mutate({ sql, parameters: values })
    } catch (error) { toast.error(extractApiError(error)) }
  }
  async function share() {
    try {
      const address = new URL(window.location.href)
      address.pathname = "/discover"
      address.search = ""
      address.searchParams.set("mode", "sql")
      address.searchParams.set("sql", sql)
      address.searchParams.set("parameters", parameters)
      await navigator.clipboard.writeText(address.toString())
      toast.success("Query link copied. Results may change as the corpus grows.")
    } catch (error) { toast.error(extractApiError(error)) }
  }
  function download() {
    if (!query.data) return
    const csv = [query.data.columns, ...query.data.rows].map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")
    const address = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }))
    const link = document.createElement("a"); link.href = address; link.download = "periplus-results.csv"; link.click()
    URL.revokeObjectURL(address)
  }
  return <section aria-label="SQL workspace" className="bench-page">
    <section aria-labelledby="sql-headline" className="flex flex-col gap-6">
      <div><h2 id="sql-headline" className="text-xl font-medium tracking-tight">SQL bench</h2><p className="mt-2 text-muted-foreground">Start with a dataset query, change the extraction, and export returned rows. Source links open live websites; collection dates describe the captured data.</p></div>
      <div id="explore" className="flex scroll-mt-6 flex-col gap-4" aria-label="SQL workspace">
        <div className="sql-studio">
          <div className="sql-studio-toolbar">
            <span className="flex items-center gap-2 font-mono text-xs"><span className="size-1.5 rounded-full bg-emerald-300" />explore.sql</span>
            <div className="flex flex-wrap gap-1">{examples.map(example => <Button key={example.label} variant="ghost" className="sql-example" aria-pressed={example.sql === sql} onClick={() => loadSql(example.sql)}>{example.label}</Button>)}</div>
          </div>
          <div ref={editor} onKeyDownCapture={event => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); event.stopPropagation(); if (!query.isPending && sql.trim()) run() } }}><SqlEditor value={sql} onChange={setSql} /></div>
          <div className="sql-studio-actions">
            <span id="editor-help" className="sql-editor-help">SQL completion · ⌘ / Ctrl + Enter to run · Tab to leave editor</span>
            <div className="flex items-center gap-2"><Button variant="ghost" className="sql-share" size="icon" onClick={share} aria-label="Share SQL"><Share2 /></Button><Button className="sql-run" size="lg" disabled={query.isPending || !sql.trim()} onClick={run}><Play className={query.isPending ? "animate-pulse motion-reduce:animate-none" : ""} />{query.isPending ? "Running query…" : "Run query"}</Button></div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground"><span aria-live="polite">{activeExample?.description ?? "Your SQL defines the fields, filters, and input sample."}</span><span>Read-only · DuckDB SQL · 1,000 rows / 8 MiB · 20s</span></div>
        <details className="text-sm text-muted-foreground"><summary className="cursor-pointer">Schema & query options</summary><p className="py-3"><Link className="story-link" href="/docs#schema">Full schema reference & join guidance →</Link></p><div className="grid gap-5 py-4 sm:grid-cols-2">{relations.map(relation => <div key={relation.name}><Button variant="link" onClick={() => loadSql(`DESCRIBE ${relation.name};`)}>{relation.name}</Button><p>{relation.grain}. {relation.detail}</p></div>)}</div><label htmlFor="parameters">Positional parameters (JSON array)</label><Textarea id="parameters" value={parameters} onChange={event => setParameters(event.target.value)} /><p className="py-2">Queries and parameters are logged to improve Periplus. Shared links contain both. Do not include secrets.</p></details>
      {query.error && <Alert variant="destructive"><AlertDescription>{extractApiError(query.error)} Your SQL is still in the editor.</AlertDescription></Alert>}
      {query.data ? <Card aria-label="Query results"><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle><h3>Results</h3></CardTitle><CardDescription>{query.data.rows.length} rows · {(query.data.elapsed_ms / 1000).toFixed(2)}s{query.data.sql !== sql ? " · from your previous query" : ""}</CardDescription></div><Button variant="outline" onClick={download}><Download />Export CSV</Button></div></CardHeader><CardContent className="flex flex-col gap-4">
        {query.data.diagnostics.map(item => <Alert key={item.code}><AlertDescription>{item.message}</AlertDescription></Alert>)}
        {query.data.truncated && <Alert><AlertDescription>Showing a partial result: the row or response-size limit was reached. Export includes only these displayed rows.</AlertDescription></Alert>}
        <QueryTable columns={query.data.columns} types={query.data.types} rows={query.data.rows} />
        {!query.data.rows.length && <p>No matching rows. Try a broader filter or explore site coverage to see what’s available.</p>}
        <details className="bench-execution"><summary>SQL, execution plan & query reference</summary><p>{query.data.query_id}</p><pre className="overflow-auto">{query.data.sql}</pre><pre className="overflow-auto">{query.data.plan}</pre></details>
      </CardContent></Card> : null}
      </div>
    </section>
    <footer className="flex flex-col gap-3 pb-6"><Separator /><div className="flex flex-wrap justify-between gap-3"><CardDescription><Link href="/datasets">Datasets</Link> · <Link href="/coverage">Coverage</Link> · <Link href="/docs">Schema & SQL docs</Link> · <Link href="/about#access">Data use</Link></CardDescription><CardDescription>CSV includes returned rows only; a LIMIT can make the result partial.</CardDescription></div></footer>
  </section>
}
