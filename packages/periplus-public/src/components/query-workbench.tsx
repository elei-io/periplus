"use client"

import { memo, useCallback, useEffect, useRef, useState } from "react"
import { Database, ListTree, MessageSquare, Play, Share2, Table2 } from "lucide-react"
import { toast } from "sonner"
import dynamic from "next/dynamic"

import { Button } from "@/components/ui/button"
import { QuerySettings } from "@/components/query-settings"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { schemaReference } from "@/lib/schema-reference"
import { Separator } from "@/components/ui/separator"
import { QueryExport } from "@/components/query-export"
import { QueryTable } from "@/components/query-table"
import { useQueryExecution } from "@/hooks/use-query-execution"
import Link from "next/link"
import { consumeDiscoveryLaunch } from "@/lib/discovery-launch"
import { extractApiError } from "@/lib/api"
import { useSqlAssistant } from "@/hooks/use-sql-assistant"
import { SqlAssistant } from "@/components/sql-assistant"
import posthog from "posthog-js"

const SqlEditor = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor), { ssr: false, loading: () => <div className="sql-loading">Loading SQL editor…</div> })



export function QueryWorkbench({ initialSql, initialParameters, autoRun = false }: { initialSql?: string; initialParameters?: string; autoRun?: boolean }) {
  const [sql, setSql] = useState(initialSql ?? "")
  const [parameters, setParameters] = useState(initialParameters ?? "[]")
  const [selection, setSelection] = useState("")
  const editor = useRef<HTMLDivElement>(null)
  const query = useQueryExecution()
  const assistant = useSqlAssistant({ sql, parameters }, selection, query.error && query.variables ? {
    sql: query.variables.sql, parameters: JSON.stringify(query.variables.parameters), message: extractApiError(query.error).slice(0, 2_000),
  } : null, draft => { setSql(draft.sql); setParameters(draft.parameters) })
  const { mutate } = query
  useEffect(() => {
    if (!query.access.enabled || !autoRun || !initialSql?.trim() || !consumeDiscoveryLaunch()) return
    try {
      const values: unknown = JSON.parse(initialParameters ?? "[]")
      if (!Array.isArray(values)) throw new Error("Parameters must be a JSON array.")
      mutate({ sql: initialSql, parameters: values })
    } catch (error) { toast.error(extractApiError(error)) }
  }, [autoRun, initialSql, initialParameters, mutate, query.access.enabled])
  const loadSql = useCallback((value: string) => {
    setSql(value); setParameters("[]")
    editor.current?.querySelector<HTMLElement>('[contenteditable="true"]')?.focus()
    document.getElementById("explore")?.scrollIntoView({ block: "start" })
  }, [])
  function run() {
    if (!query.access.enabled) return
    try {
      const values: unknown = JSON.parse(parameters)
      if (!Array.isArray(values)) throw new Error("Parameters must be a JSON array.")
      query.mutate({ sql, parameters: values })
      posthog.capture("sql_query_run", {
        has_parameters: Array.isArray(values) && values.length > 0,
        sql_length: sql.length,
      })
    } catch (error) { toast.error(extractApiError(error)) }
  }
  async function share() {
    try {
      const address = new URL(window.location.href)
      address.pathname = "/sql"
      address.search = ""
      address.searchParams.set("sql", sql)
      address.searchParams.set("parameters", parameters)
      await navigator.clipboard.writeText(address.toString())
      toast.success("Query link copied. Results may change as the corpus grows.")
      posthog.capture("sql_query_shared", {
        sql_length: sql.length,
      })
    } catch (error) { toast.error(extractApiError(error)) }
  }
  return <section aria-label="SQL workspace" className="sql-workbench flex flex-col gap-4">
    <div className="grid items-start gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      <SchemaExplorer onLoadSql={loadSql} />
      <div id="explore" className="flex min-w-0 scroll-mt-6 flex-col gap-4">
        <Card size="sm" className="sql-input-surface gap-0 py-0">
          <div className="flex flex-wrap items-center gap-2 p-3"><Button disabled={!query.access.enabled || query.isPending || !sql.trim()} onClick={run}><Play />{query.isPending ? "Running…" : "Run query"}</Button><Button variant="outline" onClick={share}><Share2 />Share</Button><QuerySettings value={parameters} onChange={setParameters} /><Button variant="ghost" aria-expanded={assistant.open} aria-controls="sql-assistant" onClick={() => { if (assistant.open) { assistant.setOpen(false) } else { assistant.show(); posthog.capture("sql_assistant_opened") } }}><MessageSquare />Ask SQL</Button><CardDescription id="editor-help" className="ml-auto">⌘ / Ctrl + Enter to run · Tab to leave editor</CardDescription></div>
          <div ref={editor} onKeyDownCapture={event => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); event.stopPropagation(); if (!query.isPending && sql.trim()) run() } }}><SqlEditor value={sql} onChange={setSql} onSelectionChange={setSelection} /></div>
          {!sql.trim() && !assistant.open && <div className="px-3"><Button variant="ghost" size="sm" onClick={() => assistant.show()}>Describe what you want to query…</Button></div>}
          <div className="flex flex-wrap items-center justify-between gap-2 p-3"><div className="flex flex-wrap items-center gap-3"><CardDescription role="status">{query.access.message ?? query.phase}</CardDescription><Badge variant="secondary">DuckDB SQL · Read-only</Badge></div><CardDescription>{query.access.data ? `${query.access.data.sql.max_rows.toLocaleString()} rows / ${query.access.data.sql.max_result_bytes / (1024 * 1024)} MiB · ${query.access.data.sql.max_duration_seconds}s limit` : query.access.message === "Checking availability…" ? "Loading query limits…" : "Query limits unavailable"}</CardDescription></div>
          <SqlAssistant assistant={assistant} />
        </Card>
        <Card size="sm" aria-label="Query output" className="sql-output-surface min-h-72" aria-busy={query.isPending}>
          <Tabs defaultValue="results">
            <div className="flex flex-wrap items-center justify-between gap-3 px-3"><TabsList variant="line" aria-label="Query output"><TabsTrigger value="results"><Table2 />Results</TabsTrigger><TabsTrigger value="plan">Execution plan</TabsTrigger></TabsList><QueryExport result={query.data} disabled={query.isPending} /></div>
            <Separator />
            <CardContent className="flex flex-col gap-3 pt-3">
              {query.error && <Alert variant="destructive"><AlertDescription>{extractApiError(query.error)} Your SQL is still in the editor.<Button variant="ghost" size="sm" onClick={() => assistant.show("Help fix the latest query error while preserving what the query is meant to return.")}>Help fix</Button></AlertDescription></Alert>}
              <CardDescription key={`${query.submittedAt}-${query.status}`} className={query.isSuccess ? "query-complete" : undefined} role="status">{query.isPending ? "Executing query… Results will appear here." : query.data ? `${query.data.rows.length} rows · ${(query.data.elapsed_ms / 1000).toFixed(2)}s${query.data.sql !== sql || query.error ? " · from your previous query" : ""}` : "Ready to query"}</CardDescription>
              {query.data?.diagnostics.filter(item => item.code !== "plan_truncated").map(item => <Alert key={item.code}><AlertDescription>{item.message}</AlertDescription></Alert>)}
              {query.data?.truncated && <Alert><AlertDescription>Partial result: the row or response-size limit was reached. Export includes only displayed rows.</AlertDescription></Alert>}
            </CardContent>
            <TabsContent value="results" className="sql-results-viewport min-w-0 px-3">
              {query.data ? <><QueryTable columns={query.data.columns} types={query.data.types} rows={query.data.rows} />{!query.data.rows.length && <CardDescription>No matching rows. Try a broader filter.</CardDescription>}</> : <div className="flex min-h-40 flex-col items-center justify-center gap-3"><Table2 className="size-6" /><CardTitle>Your results appear here</CardTitle><CardDescription>Write SQL above and run it to inspect the returned rows.</CardDescription></div>}
            </TabsContent>
            <TabsContent value="plan" className="min-w-0 px-3">{query.data ? <div className="flex flex-col gap-3"><CardDescription>Query reference: {query.data.query_id}</CardDescription>{query.data.diagnostics.filter(item => item.code === "plan_truncated").map(item => <Alert key={item.code}><AlertDescription>{item.message}</AlertDescription></Alert>)}<pre className="overflow-auto">{query.data.plan || "No execution plan returned."}</pre><details><summary className="cursor-pointer">Executed SQL</summary><pre className="overflow-auto py-3">{query.data.sql}</pre></details></div> : <CardDescription className="py-8">Run a query to inspect its execution plan.</CardDescription>}</TabsContent>
          </Tabs>
        </Card>
      </div>
    </div>
  </section>
}

const SchemaExplorer = memo(function SchemaExplorer({ onLoadSql }: { onLoadSql: (sql: string) => void }) {
  const [filter, setFilter] = useState("")
  function inspectTable(tableName: string) {
    onLoadSql(`DESCRIBE ${tableName};`)
    posthog.capture("schema_table_inspected", { table_name: tableName })
  }
  return (
      <Card id="schema-explorer" size="sm" className="sql-support-surface min-w-0">
        <CardHeader><CardTitle className="flex items-center gap-2"><Database className="size-4" />Explorer</CardTitle><Input aria-label="Filter tables and columns" placeholder="Filter schema…" value={filter} onChange={event => setFilter(event.target.value)} /></CardHeader>
        <CardContent className="max-h-96 overflow-auto lg:max-h-[640px]">
          <TooltipProvider>
            {["public_v1"].map(namespace => {
              const relations = schemaReference.filter(relation => relation.name.startsWith(`${namespace}.`) && `${relation.name} ${relation.columns.map(column => column[0]).join(" ")}`.toLowerCase().includes(filter.toLowerCase()))
              relations.sort((a, b) => a.name.length - b.name.length || a.name.localeCompare(b.name))
              if (!relations.length) return null
              return <div key={namespace} className="pb-4">
                <p className="px-1 pb-2 font-mono text-xs text-muted-foreground">{namespace}</p>
                {relations.map(relation => <div key={relation.name} className="relative"><details open={filter ? true : undefined} className="py-1">
                  <summary className="cursor-pointer py-1 pr-8" title={relation.grain}><span className="inline-flex items-center gap-2"><Table2 className="size-3.5" /><span>{relation.name.slice(namespace.length + 1)}</span></span></summary>
                  <div className="flex min-w-0 flex-col gap-1 py-2 pl-5">
                    {relation.columns.map(([name, type, description]) => <Tooltip key={name}>
                      <TooltipTrigger render={<span tabIndex={0} className="block truncate py-1 font-mono text-xs text-muted-foreground" />}>{name}</TooltipTrigger>
                      <TooltipContent side="right"><div className="flex flex-col gap-1"><code>{name} · {type}</code><span>{description}</span></div></TooltipContent>
                    </Tooltip>)}
                  </div>
                </details><Tooltip><TooltipTrigger render={<Button variant="ghost" size="icon-sm" className="absolute top-1 right-0" aria-label={`Inspect schema of ${relation.name}`} onClick={() => inspectTable(relation.name)} />}><ListTree /></TooltipTrigger><TooltipContent>Inspect schema</TooltipContent></Tooltip></div>)}
              </div>
            })}
          </TooltipProvider>
          {!schemaReference.some(relation => `${relation.name} ${relation.columns.map(column => column[0]).join(" ")}`.toLowerCase().includes(filter.toLowerCase())) && <CardDescription>No tables or columns match.</CardDescription>}
        </CardContent>
        <Separator />
        <CardContent><Link href="/docs#schema">Schema reference ↗</Link></CardContent>
      </Card>
  )
})
