"use client"

import { useState } from "react"
import dynamic from "next/dynamic"
import Markdown from "react-markdown"
import { ArrowUpRight, Copy, Download, Database } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { QueryTable } from "@/components/query-table"
import { analysisCsv, analysisView } from "@/lib/analysis-view"
import { displayValue } from "@/lib/query-values"
import { extractApiError } from "@/lib/api"
import type { DiscoveryMessage } from "@/types/assistant"
import type { AnalysisQueryResult } from "@/types/analysis"

const SqlStatement = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor), { ssr: false, loading: () => <p>Loading SQL…</p> })

function SqlActions({ sql, onOpenSql }: { sql: string; onOpenSql: (sql: string) => void }) {
  return <div className="analysis-actions"><Button variant="outline" size="sm" onClick={() => onOpenSql(sql)}>Open in SQL <ArrowUpRight /></Button><Button variant="ghost" size="sm" onClick={async () => { try { await navigator.clipboard.writeText(sql); toast.success("SQL copied.") } catch (error) { toast.error(extractApiError(error)) } }}><Copy />Copy SQL</Button></div>
}

function QueryRows({ result }: { result: AnalysisQueryResult }) {
  return <QueryTable columns={result.columns} types={result.types} rows={result.rows} />
}

function Method({ result, onOpenSql }: { result: AnalysisQueryResult; onOpenSql: (sql: string) => void }) {
  const [open, setOpen] = useState(false)
  return <details className="analysis-method" onToggle={event => setOpen(event.currentTarget.open)}><summary>View SQL & execution details</summary>{open && <div className="analysis-method-body"><SqlStatement value={result.sql} readOnly /><SqlActions sql={result.sql} onOpenSql={onOpenSql} /><p className="analysis-reference">Query {result.query_id}<br />Executed {result.executed_at} · {(result.elapsed_ms / 1000).toFixed(2)}s</p><details><summary>Execution plan</summary><pre className="overflow-auto text-xs">{result.plan}</pre></details></div>}</details>
}

function ResultBlock({ title, result, onOpenSql }: { title: string; result: AnalysisQueryResult; onOpenSql: (sql: string) => void }) {
  const scalar = result.rows.length === 1 && result.columns.length === 1
  function download() {
    try {
      const url = URL.createObjectURL(new Blob([analysisCsv(result.columns, result.rows)], { type: "text/csv;charset=utf-8" }))
      const link = document.createElement("a"); link.href = url; link.download = `periplus-${result.query_id}.csv`; link.click()
      URL.revokeObjectURL(url)
    } catch (error) { toast.error(extractApiError(error)) }
  }
  return <section className="analysis-result" aria-label={title}>
    <header><div><span className="eyebrow">Query result</span><h3>{title}</h3></div><Button variant="ghost" size="sm" onClick={download}><Download />Export CSV</Button></header>
    {scalar ? <div className="analysis-scalar"><span>{result.columns[0]}</span><strong>{displayValue(result.rows[0][0])}</strong><span>{result.types[0]}</span></div> : <QueryRows result={result} />}
    {!result.rows.length && <p className="analysis-note">No matching rows were returned. This does not establish that the information is absent from the web.</p>}
    <div className="analysis-context"><span>{result.rows.length} {result.rows.length === 1 ? "row" : "rows"}{result.truncated ? " · partial result" : ""} · collected data</span><span>{(result.elapsed_ms / 1000).toFixed(2)}s</span></div>
    {result.truncated && <p className="analysis-note">The response was truncated to the query or display budget. The table and CSV contain only these returned rows.</p>}
    {result.diagnostics.map((item, index) => <p key={index} className="analysis-note">{item.message}</p>)}
    <Method result={result} onOpenSql={onOpenSql} />
  </section>
}

export function AnalysisAnswer({ message, onOpenSql, running }: { message: DiscoveryMessage; onOpenSql: (sql: string) => void; running: boolean }) {
  const { finding, presentation, draft, queries } = analysisView(message)
  const [activityOpen, setActivityOpen] = useState(false)
  return <>
    {finding && <div className="analysis-finding"><Markdown skipHtml components={draft ? { pre: () => null } : undefined} allowedElements={["p", "strong", "em", "code", "pre", "ul", "ol", "li", "br", "a"]} unwrapDisallowed>{finding}</Markdown></div>}
    {presentation?.results?.map(({ title, result }) => <ResultBlock key={result.query_id} title={title} result={result} onOpenSql={onOpenSql} />)}
    {presentation && <p className="analysis-scope"><strong>Analysis scope</strong>{presentation.context}</p>}
    {draft && <section className="analysis-result"><header><div><span className="eyebrow">SQL draft · not executed</span><h3>{draft.title}</h3></div></header><SqlStatement value={draft.sql} readOnly /><div className="analysis-method-body"><SqlActions sql={draft.sql} onOpenSql={onOpenSql} /><p className="analysis-note">This draft has not been executed or validated by the query API.</p></div></section>}
    {queries.length > 0 && <details className="analysis-activity" onToggle={event => setActivityOpen(event.currentTarget.open)}><summary><Database />Query activity · {queries.length} {queries.length === 1 ? "query" : "queries"}{running ? " · working" : ""}</summary>{activityOpen && <div className="analysis-activity-body">{queries.map(part => <details key={part.toolCallId}><summary>{part.state === "output-available" ? part.output.error ? "Failed" : "Completed" : part.state === "output-error" ? "Failed" : running ? "Running" : "Incomplete"} · {part.input?.purpose ?? "Inspecting data"}</summary>{part.input?.sql && <pre className="overflow-auto text-xs">{part.input.sql}</pre>}{part.state === "output-available" && part.output.error && <p>{part.output.error}</p>}{part.state === "output-error" && <p>The query could not complete.</p>}{part.state === "output-available" && part.output.result && <><p className="analysis-note">{part.output.result.rows.length} rows{part.output.result.truncated ? " · partial result" : ""} · {part.output.result.query_id}</p><QueryRows result={part.output.result} /></>}</details>)}</div>}</details>}
  </>
}
