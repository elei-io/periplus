"use client"

import Link from "next/link"
import { useState } from "react"
import dynamic from "next/dynamic"
import Markdown from "react-markdown"
import { ArrowUpRight, Copy, Download, Database } from "lucide-react"
import { toast } from "sonner"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { QueryTable } from "@/components/query-table"
import { analysisCsv, analysisView } from "@/lib/analysis-view"
import { displayValue } from "@/lib/query-values"
import { extractApiError } from "@/lib/api"
import type { DiscoveryMessage } from "@/types/assistant"
import type { AnalysisQueryResult } from "@/types/analysis"

const SqlStatement = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor), { ssr: false, loading: () => <p>Loading SQL…</p> })

function SqlActions({ sql }: { sql: string }) {
  return <div className="analysis-actions"><Button variant="outline" size="sm" nativeButton={false} render={<Link href={`/sql?${new URLSearchParams({ sql })}`} target="_blank" rel="noopener noreferrer" />}>Open SQL workspace <ArrowUpRight /></Button><Button variant="ghost" size="sm" onClick={async () => { try { await navigator.clipboard.writeText(sql); toast.success("SQL copied.") } catch (error) { toast.error(extractApiError(error)) } }}><Copy />Copy SQL</Button></div>
}

function QueryRows({ result }: { result: AnalysisQueryResult }) {
  return <QueryTable columns={result.columns} types={result.types} rows={result.rows} />
}

function Method({ result }: { result: AnalysisQueryResult }) {
  const [open, setOpen] = useState(false)
  return <details className="analysis-method" onToggle={event => setOpen(event.currentTarget.open)}><summary>View SQL & execution details</summary>{open && <div className="analysis-method-body"><SqlStatement value={result.sql} readOnly /><SqlActions sql={result.sql} /><p className="analysis-reference">Query {result.query_id} · source snapshot {result.source_snapshot}<br />Executed {result.executed_at} · {(result.elapsed_ms / 1000).toFixed(2)}s</p><details><summary>Execution plan</summary><pre className="overflow-auto text-xs">{result.plan}</pre></details></div>}</details>
}

function ResultBlock({ title, result, anchor }: { anchor: string; title: string; result: AnalysisQueryResult }) {
  const scalar = result.rows.length === 1 && result.columns.length === 1
  function download() {
    try {
      const url = URL.createObjectURL(new Blob([analysisCsv(result.columns, result.rows)], { type: "text/csv;charset=utf-8" }))
      const link = document.createElement("a"); link.href = url; link.download = `periplus-${result.query_id}.csv`; link.click()
      URL.revokeObjectURL(url)
    } catch (error) { toast.error(extractApiError(error)) }
  }
  return <section id={anchor} className="analysis-result" aria-label={title}>
    <header><div><span className="eyebrow">Query result</span><h3>{title}</h3></div><Button variant="ghost" size="sm" onClick={download}><Download />Export CSV</Button></header>
    {scalar ? <div className="analysis-scalar"><span>{result.columns[0]}</span><strong>{displayValue(result.rows[0][0])}</strong><span>{result.types[0]}</span></div> : <QueryRows result={result} />}
    {!result.rows.length && <p className="analysis-note">No matching rows were returned. This does not establish that the information is absent from the web.</p>}
    <div className="analysis-context"><span>{result.rows.length} {result.rows.length === 1 ? "row" : "rows"}{result.truncated ? " · partial result" : ""} · SQL output</span><span>{(result.elapsed_ms / 1000).toFixed(2)}s</span></div>
    {result.truncated && <p className="analysis-note">The response was truncated to the query or display budget. The table and CSV contain only these returned rows.</p>}
    {result.diagnostics.map((item, index) => <p key={index} className="analysis-note">{item.message}</p>)}
    <Method result={result} />
  </section>
}

export function AnalysisAnswer({ message, running }: { message: DiscoveryMessage; running: boolean }) {
  const { finding, presentation, draft, queries } = analysisView(message)
  const [activityOpen, setActivityOpen] = useState(false)
  const anchor = (queryId: string) => `evidence-${message.id}-${queryId}`
  return <>
    {finding && !presentation && <div className="analysis-finding"><span className="eyebrow">Dataset design</span><Markdown skipHtml components={draft ? { pre: () => null } : undefined} allowedElements={["p", "strong", "em", "code", "pre", "ul", "ol", "li", "br", "a"]} unwrapDisallowed>{finding}</Markdown></div>}
    {presentation && (() => {
      const dataset = presentation.results.find(item => item.result.query_id === presentation.dataset_query_id)
      const evidence = presentation.results.filter(item => item.result.query_id !== presentation.dataset_query_id)
      return <Card>
        <CardHeader><div className="flex items-center justify-between gap-3"><CardTitle>{dataset ? "Dataset preview" : "What I found"}</CardTitle><Badge variant="secondary">{({ designing: "Definition in progress", ready: "Dataset ready", collection_needed: "Sources needed", not_fit: "Unsupported requirement", blocked: "Build interrupted" })[presentation.outcome.status]}</Badge></div><CardDescription>{presentation.outcome.next_step}{dataset && " These results belong to the last executed definition. Rebuild after editing."}</CardDescription></CardHeader>
        <CardContent className="flex flex-col gap-4">
          {presentation.outcome.status === "collection_needed" && <Button variant="outline" className="self-start" nativeButton={false} render={<Link href="/observatory" />}>Request missing sources<ArrowUpRight /></Button>}
          <details open={Boolean(dataset) || undefined}><summary>{dataset ? "Dataset & evidence" : "Source evidence & checks"}</summary><Tabs defaultValue={dataset ? "data" : "validation"} key={`${message.id}-${Boolean(dataset)}`}>
            <TabsList aria-label="Dataset output"><TabsTrigger value="data">Dataset</TabsTrigger><TabsTrigger value="sql">Reusable SQL</TabsTrigger><TabsTrigger value="validation">Validation</TabsTrigger></TabsList>
            <TabsContent value="data">{dataset ? <ResultBlock title={presentation.brief.title} result={dataset.result} anchor={anchor(dataset.result.query_id)} /> : <p>No dataset has been built yet. Review the definition and resolve any missing sources or decisions.</p>}</TabsContent>
            <TabsContent value="sql"><div className="flex flex-col gap-4">{dataset ? <><SqlStatement value={dataset.result.sql} readOnly /><SqlActions sql={dataset.result.sql} /><p>This standalone query produces the dataset above. Rerunning uses the data available then; new observations and source layouts can change the result. Re-run validation when inputs change.</p></> : <p>Executed dataset SQL will appear here once the definition can be built.</p>}<Button variant="outline" className="self-start" onClick={() => {
              try {
                const artifact = { specification: presentation.brief, source_plan: presentation.source_plan ? { material: presentation.source_plan.material, approach: presentation.source_plan.approach, sql: presentation.source_plan.evidence.map(item => item.result.sql) } : null, sql: dataset?.result.sql ?? null, source_snapshot: dataset?.result.source_snapshot ?? null, executed_at: dataset?.result.executed_at ?? null, status: presentation.outcome.status, validation: presentation.validation.map(({ evidence, ...check }) => ({ ...check, sql: evidence.map(item => item.result.sql) })), limitations: presentation.outcome.limitations, repeatability: "SQL runs against available observations. A snapshot identifier records the source; it does not pin future executions. Revalidate after source or layout changes." }
                const url = URL.createObjectURL(new Blob([JSON.stringify(artifact, null, 2)], { type: "application/json" }))
                const link = document.createElement("a"); link.href = url; link.download = "periplus-dataset-definition.json"; link.click(); URL.revokeObjectURL(url)
              } catch (error) { toast.error(extractApiError(error)) }
            }}><Download />{dataset ? "Download definition & SQL" : "Download definition"}</Button></div></TabsContent>
            <TabsContent value="validation"><div className="flex flex-col gap-4">
              {presentation.validation.length ? presentation.validation.map((check, index) => <Card key={index}><CardHeader><CardTitle>{check.check} · {check.status}</CardTitle><CardDescription>{check.detail}</CardDescription></CardHeader><CardContent>{check.evidence.map(({ result }) => <details key={result.query_id}><summary>Inspect check SQL & rows</summary><SqlStatement value={result.sql} readOnly /><SqlActions sql={result.sql} /><QueryRows result={result} /></details>)}</CardContent></Card>) : <p>No validation checks have been executed yet.</p>}
              <dl className="grid gap-3">{([["Schema & grain", presentation.outcome.requested_information], ["Source fidelity", presentation.outcome.source_fidelity], ["Missing data & limits", presentation.outcome.limitations], ["Scope", presentation.context]] as const).map(([label, value]) => <div key={label}><dt><strong>{label}</strong></dt><dd>{value}</dd></div>)}</dl>
              {presentation.source_plan && <details><summary>Available material & extraction evidence</summary><p>{presentation.source_plan.approach}</p>{presentation.source_plan.evidence.map(({ title, result }) => <ResultBlock key={result.query_id} title={title} result={result} anchor={`${anchor(result.query_id)}-source`} />)}</details>}
              {evidence.map(({ title, result }) => <details key={result.query_id}><summary>{title}</summary><ResultBlock title={title} result={result} anchor={anchor(result.query_id)} /></details>)}
            </div></TabsContent>
          </Tabs></details>
        </CardContent>
      </Card>
    })()}
    {!running && queries.length > 0 && !presentation && !draft && <p>The build stopped before producing a dataset definition. Continue below or inspect query activity.</p>}
    {draft && <section className="analysis-result"><header><div><span className="eyebrow">SQL draft · not executed</span><h3>{draft.title}</h3></div></header><SqlStatement value={draft.sql} readOnly /><div className="analysis-method-body"><SqlActions sql={draft.sql} /><p className="analysis-note">This draft has not been executed or validated by the query API.</p></div></section>}
    {queries.length > 0 && <details className="analysis-activity" onToggle={event => setActivityOpen(event.currentTarget.open)}><summary><Database />Query activity · {queries.length} {queries.length === 1 ? "query" : "queries"}{running ? " · working" : ""}</summary>{activityOpen && <div className="analysis-activity-body">{queries.map(part => <details key={part.toolCallId}><summary>{part.state === "output-available" ? part.output.error ? "Failed" : "Completed" : part.state === "output-error" ? "Failed" : running ? "Running" : "Incomplete"} · {part.input?.purpose ?? "Inspecting data"}</summary>{part.input?.sql && <pre className="overflow-auto text-xs">{part.input.sql}</pre>}{part.state === "output-available" && part.output.error && <p>{part.output.error}</p>}{part.state === "output-error" && <p>The query could not complete.</p>}{part.state === "output-available" && part.output.result && <><p className="analysis-note">{part.output.result.rows.length} rows{part.output.result.truncated ? " · partial result" : ""} · {part.output.result.query_id}</p><QueryRows result={part.output.result} /></>}</details>)}</div>}</details>}
  </>
}
