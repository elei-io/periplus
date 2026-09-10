"use client"

import Link from "next/link"
import { ArrowUpRight, Download } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { QueryTable, QueryTableLoading, QueryValue } from "@/components/query-table"
import { captureAnalytics } from "@/lib/analytics"
import { analysisCsv, analysisView } from "@/lib/analysis-view"
import { extractApiError } from "@/lib/api"
import type { DiscoveryMessage } from "@/types/assistant"
import { datasetBriefSchema, sameDatasetBrief, type DatasetBrief, type DatasetMode } from "@/types/answer"

function download(name: string, text: string, type: string) {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }))
    const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url)
    return true
  } catch (error) { toast.error(extractApiError(error)) }
}

export function DatasetResult({ message, mode, contract, busy, previous, buildHref }: { message?: DiscoveryMessage; mode: DatasetMode; contract?: DatasetBrief; busy: boolean; previous: boolean; buildHref: string }) {
  const presentation = message ? analysisView(message).presentation : undefined
  const dataset = presentation?.dataset?.rows.length ? presentation.dataset : undefined
  const imageIndex = dataset?.columns.findIndex(name => /^(thumbnail|thumbnail_url|image|image_url)$/i.test(name)) ?? -1
  const order = dataset?.columns.map((_, index) => index) ?? []
  if (mode === "discover" && imageIndex >= 0) { order.splice(imageIndex, 1); order.unshift(imageIndex) }
  const ready = mode === "build" && presentation?.status === "ready" && sameDatasetBrief(contract, presentation.brief)
  const contractChanged = mode === "build" && presentation?.status === "ready" && !ready
  return <Card className="dataset-surface min-w-0">
    <CardHeader><div className="flex flex-wrap items-center justify-between gap-2"><CardTitle>{presentation?.brief.title ?? (mode === "discover" ? "What can you find here?" : "Dataset preview")}</CardTitle><Badge variant={ready ? "default" : "secondary"}>{busy ? "Querying data…" : ready ? "Validated" : dataset ? mode === "discover" ? "Findings" : "Not validated" : presentation ? "No preview yet" : "No dataset yet"}</Badge></div><CardDescription>{dataset ? `${dataset.rows.length} displayed ${dataset.rows.length === 1 ? "row" : "rows"}` : mode === "discover" ? "Start with a question. Explore real records, see what is available, and refine from there." : contract ? datasetBriefSchema.safeParse(contract).success ? "Your schema is ready to check. Choose Validate dataset in the Schema tab to retrieve rows and check the requirements." : "Complete the unfinished fields in the Schema tab, then choose Validate dataset." : "Define your columns or describe the dataset. Real rows and contract checks will appear here."}</CardDescription></CardHeader>
    <CardContent className="flex min-w-0 flex-col gap-4">
      {!contractChanged && presentation?.issues.length ? <ul aria-label="Contract validation issues">{presentation.issues.map(issue => <li key={issue}>{issue}</li>)}</ul> : null}
      {contractChanged && <p role="status">The schema has changed since this result. Validate the current contract from the Schema tab.</p>}
      {busy && !dataset && <QueryTableLoading />}
      {previous && dataset && <p role="status">Showing the previous result. The latest request has not produced a replacement.</p>}
      {dataset && <>
        <QueryTable label={mode === "discover" ? "Discovery findings" : "Dataset preview"} columns={order.map(index => mode === "discover" ? dataset.columns[index].replaceAll("_", " ") : dataset.columns[index])} types={order.map(index => dataset.types[index])} showTypes={mode === "build"} imageColumns={imageIndex >= 0 ? [order.indexOf(imageIndex)] : []} rows={dataset.rows.map(row => order.map(index => row[index]))} renderCell={(value, displayIndex) => {
          const index = order[displayIndex]
          if (/^(thumbnail|thumbnail_url|image|image_url)$/i.test(dataset.columns[index]) && typeof value === "string" && /^https?:\/\//i.test(value)) {
            // Source URLs are returned by SQL, never fetched by the Next.js image proxy.
            // eslint-disable-next-line @next/next/no-img-element
            return <a href={value} target="_blank" rel="noopener noreferrer"><img src={value} alt="Listing thumbnail" width={72} height={72} loading="lazy" referrerPolicy="no-referrer" /></a>
          }
          return <QueryValue value={value} type={dataset.types[index]} />
        }} />
        {presentation?.limitations && <p>{presentation.limitations}</p>}
        <details className="workspace-disclosure"><summary>Sources and scope</summary><p>Snapshot {dataset.source_snapshot}</p><p>{presentation?.brief.population}</p><p>{presentation?.brief.grain}</p></details>
        {dataset.truncated && <p>Results were truncated by the query service. Downloads contain only the displayed rows.</p>}
        <div className="flex flex-wrap gap-2">
          {mode === "discover" && <Button disabled={busy} nativeButton={false} render={<Link href={buildHref} target="_blank" rel="noopener noreferrer" />}>Build this dataset<ArrowUpRight /></Button>}
          <Button variant={ready ? "default" : "outline"} onClick={() => download(ready ? "periplus-dataset.csv" : "periplus-preview.csv", analysisCsv(dataset.columns, dataset.rows), "text/csv;charset=utf-8") && captureAnalytics("dataset_export_initiated", { workspace: mode, format: "csv", validated: ready, row_count: dataset.rows.length, truncated: dataset.truncated, operation_id: message?.metadata?.operation_id, result_id: message?.id })}><Download />{ready ? "Download dataset" : "Download CSV"}</Button>
          <Button variant="ghost" nativeButton={false} render={<Link href={`/sql?${new URLSearchParams({ sql: dataset.sql })}`} target="_blank" rel="noopener noreferrer" />}>Open SQL<ArrowUpRight /></Button>
          {ready && <Button variant="ghost" onClick={() => download("periplus-dataset.json", JSON.stringify({ brief: presentation!.brief, sql: dataset.sql, schema_version: dataset.schema_version, source_snapshot: dataset.source_snapshot, checks: presentation!.checks.map(check => check.sql) }, null, 2), "application/json") && captureAnalytics("dataset_definition_saved", { workspace: mode, validated: ready, field_count: presentation!.brief.fields.length, operation_id: message?.metadata?.operation_id })}><Download />Save definition</Button>}
        </div>
      </>}
      {presentation?.checks && presentation.checks.length > 0 && <details open={!ready && !contractChanged}><summary>{contractChanged ? "Checks for previous contract" : "Validation checks"}</summary>{presentation.checks.map(check => <QueryTable key={check.query_id} columns={check.columns} types={check.types} rows={check.rows} />)}</details>}
      {!dataset && presentation?.limitations && <p>{presentation.limitations}</p>}
    </CardContent>
  </Card>
}
