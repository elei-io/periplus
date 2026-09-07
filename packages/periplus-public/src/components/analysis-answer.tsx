"use client"

import Link from "next/link"
import { useState } from "react"
import dynamic from "next/dynamic"
import Markdown from "react-markdown"
import { ArrowUpRight, Copy, Download, Database } from "lucide-react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
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

function ResultBlock({ title, result, anchor, onOpenSql }: { anchor: string; title: string; result: AnalysisQueryResult; onOpenSql: (sql: string) => void }) {
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
    <Method result={result} onOpenSql={onOpenSql} />
  </section>
}

export function AnalysisAnswer({ message, onOpenSql, running }: { message: DiscoveryMessage; onOpenSql: (sql: string) => void; running: boolean }) {
  const { finding, presentation, draft, queries } = analysisView(message)
  const [activityOpen, setActivityOpen] = useState(false)
  const anchor = (queryId: string) => `evidence-${message.id}-${queryId}`
  return <>
    {finding && !presentation && <div className="analysis-finding"><span className="eyebrow">Agent analysis</span><Markdown skipHtml components={draft ? { pre: () => null } : undefined} allowedElements={["p", "strong", "em", "code", "pre", "ul", "ol", "li", "br", "a"]} unwrapDisallowed>{finding}</Markdown></div>}
    {presentation && <section className="analysis-result" aria-label="Dataset brief">
      <header><div><span className="eyebrow">Working dataset brief · refine it in your next message</span><h3>{presentation.brief.title}</h3></div><Badge variant="secondary">{({ designing: "Designing", ready: "Dataset ready", collection_needed: "Needs collection", not_fit: "Task not a fit", blocked: "Investigation blocked" })[presentation.outcome.status]}</Badge></header>
      <dl className="analysis-method-body grid gap-3 sm:grid-cols-2">
        {([["Intended use", presentation.brief.purpose], ["One row represents", presentation.brief.grain], ["Fields & relationships", presentation.brief.fields], ["Population", presentation.brief.population], ["Time scope", presentation.brief.time_scope], ["Acceptance criteria", presentation.brief.acceptance]] as const).map(([label, value]) => <div key={label}><dt><strong>{label}</strong></dt><dd>{value}</dd></div>)}
      </dl>
      {presentation.brief.open_questions.length > 0 && <div className="analysis-method-body"><strong>Still to decide</strong><ul>{presentation.brief.open_questions.map(question => <li key={question}>{question}</li>)}</ul></div>}
    </section>}
    {presentation?.results?.map(({ title, result }) => <ResultBlock key={result.query_id} title={title} result={result} anchor={anchor(result.query_id)} onOpenSql={onOpenSql} />)}
    {presentation && presentation.analysis.length > 0 && <section className="analysis-result" aria-label="Agent analysis">
      <header><div><span className="eyebrow">Agent analysis</span><h3>Interpretation of the results</h3></div></header>
      <div className="analysis-method-body">
        <p className="analysis-note">Generated summaries and labels, linked to the supporting query results.</p>
        {presentation.analysis.map((note, index) => <div key={index}><Markdown skipHtml allowedElements={["p", "strong", "em", "code"]} unwrapDisallowed>{note.text}</Markdown><div className="analysis-actions">{note.evidence_query_ids.map(id => <Button key={id} variant="link" size="sm" render={<a href={`#${anchor(id)}`} />}>{presentation.results.find(item => item.result.query_id === id)?.title}</Button>)}</div></div>)}
      </div>
    </section>}
    {presentation && <section className="analysis-result" aria-label="Outcome check">
      <header><div><span className="eyebrow">Agent assessment</span><h3>Where we stand</h3></div><Badge variant="secondary">{({ designing: "Designing", ready: "Dataset ready", collection_needed: "Needs collection", not_fit: "Task not a fit", blocked: "Investigation blocked" })[presentation.outcome.status]}</Badge></header>
      <dl className="analysis-method-body grid gap-3">
        {([ ["Requested information", presentation.outcome.requested_information], ["Source fidelity", presentation.outcome.source_fidelity], ["Missing data & limits", presentation.outcome.limitations], ["Your next step", presentation.outcome.next_step] ] as const).map(([label, value]) => <div key={label}><dt><strong>{label}</strong></dt><dd>{value}</dd></div>)}
      </dl>
      {presentation.outcome.status === "collection_needed" && <div className="analysis-method-body"><Button variant="outline" render={<Link href="/suggest" />}>Suggest the missing coverage <ArrowUpRight /></Button></div>}
    </section>}
    {presentation && <section className="analysis-result" aria-label="Confidence"><header><h3>Confidence in this assessment</h3></header><dl className="analysis-method-body grid gap-3 sm:grid-cols-2">{(["coverage", "correctness"] as const).map(key => <div key={key}><dt><strong>{key === "coverage" ? "Coverage assessment" : "Correctness assessment"}</strong> · {presentation.confidence[key].level}</dt><dd>{presentation.confidence[key].reason}</dd></div>)}</dl></section>}
    {!running && queries.length > 0 && !presentation && !draft && <p className="analysis-note">This investigation has not yet produced a dataset assessment. Continue the conversation or inspect the query activity.</p>}
    {presentation && <p className="analysis-scope"><strong>Analysis scope</strong>{presentation.context}</p>}
    {draft && <section className="analysis-result"><header><div><span className="eyebrow">SQL draft · not executed</span><h3>{draft.title}</h3></div></header><SqlStatement value={draft.sql} readOnly /><div className="analysis-method-body"><SqlActions sql={draft.sql} onOpenSql={onOpenSql} /><p className="analysis-note">This draft has not been executed or validated by the query API.</p></div></section>}
    {queries.length > 0 && <details className="analysis-activity" onToggle={event => setActivityOpen(event.currentTarget.open)}><summary><Database />Query activity · {queries.length} {queries.length === 1 ? "query" : "queries"}{running ? " · working" : ""}</summary>{activityOpen && <div className="analysis-activity-body">{queries.map(part => <details key={part.toolCallId}><summary>{part.state === "output-available" ? part.output.error ? "Failed" : "Completed" : part.state === "output-error" ? "Failed" : running ? "Running" : "Incomplete"} · {part.input?.purpose ?? "Inspecting data"}</summary>{part.input?.sql && <pre className="overflow-auto text-xs">{part.input.sql}</pre>}{part.state === "output-available" && part.output.error && <p>{part.output.error}</p>}{part.state === "output-error" && <p>The query could not complete.</p>}{part.state === "output-available" && part.output.result && <><p className="analysis-note">{part.output.result.rows.length} rows{part.output.result.truncated ? " · partial result" : ""} · {part.output.result.query_id}</p><QueryRows result={part.output.result} /></>}</details>)}</div>}</details>}
  </>
}
