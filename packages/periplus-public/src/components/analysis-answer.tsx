"use client"

import Link from "next/link"
import { memo, useMemo, useState } from "react"
import Markdown from "react-markdown"
import { ArrowUpRight, Download } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { QueryTable } from "@/components/query-table"
import { analysisCsv, analysisView } from "@/lib/analysis-view"
import { extractApiError } from "@/lib/api"
import type { DiscoveryMessage } from "@/types/assistant"

function download(name: string, text: string, type: string) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }))
    const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url)
  } catch (error) { toast.error(extractApiError(error)) }
}

export const AnalysisAnswer = memo(function AnalysisAnswer({ message, running, current, busy, onApprove }: { message: DiscoveryMessage; running: boolean; current: boolean; busy: boolean; onApprove: (token: string) => void }) {
  const { finding, presentation, queries } = useMemo(() => analysisView(message), [message])
  const [activityOpen, setActivityOpen] = useState(false)
  const dataset = presentation?.dataset
  return <>
    {finding && !presentation && <div aria-live="polite"><Markdown skipHtml allowedElements={["p", "strong", "em", "ul", "li"]} unwrapDisallowed>{finding}</Markdown></div>}
    {presentation && <p>{presentation.message}</p>}
    {current && presentation && <>
      {presentation.needs_sources && <Button variant="outline" nativeButton={false} render={<Link href="/coverage" />}>Request broader coverage<ArrowUpRight /></Button>}
      {dataset && <section className="analysis-result" data-status={presentation.status} aria-label={presentation.status === "sample" ? "Dataset sample" : "Your dataset"}>
        <header><div><span className="eyebrow">{presentation.status === "sample" ? "Sample · review before building" : presentation.status === "ready" ? `${dataset.rows.length} rows · ready` : "Work in progress"}</span><h3>{presentation.brief.title}</h3></div></header>
        <QueryTable columns={dataset.columns} types={dataset.types} rows={dataset.rows} />
        {presentation.limitations && <p className="analysis-note">{presentation.limitations}</p>}
        {dataset.truncated && <p className="analysis-note">Only part of the result was returned. Downloads contain the displayed rows.</p>}
        <div className="analysis-actions">
          {presentation.status === "sample" && presentation.approval && <Button disabled={busy} onClick={() => onApprove(presentation.approval!)}>Build dataset<ArrowUpRight /></Button>}
          {presentation.status === "ready" && <>
            <Button onClick={() => download("periplus-dataset.csv", analysisCsv(dataset.columns, dataset.rows), "text/csv;charset=utf-8")}><Download />Download CSV</Button>
            <Button variant="outline" nativeButton={false} render={<Link href={`/sql?${new URLSearchParams({ sql: dataset.sql })}`} target="_blank" rel="noopener noreferrer" />}>Open in SQL<ArrowUpRight /></Button>
            <Button variant="ghost" onClick={() => download("periplus-dataset.json", JSON.stringify({ brief: presentation.brief, sql: dataset.sql, schema_version: dataset.schema_version, source_snapshot: dataset.source_snapshot, checks: presentation.checks.map(check => check.sql) }, null, 2), "application/json")}><Download />Save definition</Button>
          </>}
        </div>
        {presentation.status === "sample" && <p className="analysis-note">Happy with these sources and fields? Build the full dataset, or describe what you’d like to change below.</p>}
        {presentation.checks.length > 0 && <details className="analysis-method"><summary>Quality & coverage</summary><div className="analysis-method-body">{presentation.checks.map(check => <QueryTable key={check.query_id} columns={check.columns} types={check.types} rows={check.rows} />)}<p>Schema {dataset.schema_version}, snapshot {dataset.source_snapshot}. Rerunning SQL uses the captures available then; changes to sources may require revalidation.</p></div></details>}
      </section>}
      {!dataset && presentation.limitations && <p>{presentation.limitations}</p>}
    </>}
    {!running && queries.length > 0 && !presentation && <p>This attempt stopped before producing a sample or dataset. Continue below.</p>}
    {queries.length > 0 && <details className="analysis-activity" onToggle={event => setActivityOpen(event.currentTarget.open)}><summary>Technical details · {queries.length} queries</summary>{activityOpen && <div className="analysis-activity-body">{queries.map(part => <details key={part.toolCallId}><summary>{part.input?.purpose ?? "Inspecting data"}</summary><pre className="overflow-auto text-xs">{part.input?.sql}</pre>{part.state === "output-available" && <p>{part.output.error ?? `${part.output.result?.rows.length ?? 0} rows returned`}</p>}</details>)}</div>}</details>}
  </>
})
