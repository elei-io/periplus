"use client"

import Link from "next/link"
import dynamic from "next/dynamic"
import { Copy, Download, ArrowUpRight, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { useDatasetResult } from "@/hooks/use-dataset-result"
import { Button, buttonVariants } from "@/components/ui/button"
import { QueryTable, QueryTableLoading } from "@/components/query-table"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { extractApiError } from "@/lib/api"
import { analysisCsv } from "@/lib/analysis-view"
import { datasetSqlUrl } from "@/lib/datasets"
import type { Dataset } from "@/types/dataset"

const SqlEditor = dynamic(() => import("@/components/sql-editor").then(module => module.SqlEditor), { ssr: false, loading: () => <div className="sql-loading sql-loading-readonly" role="status">Loading SQL…</div> })

export function DatasetActions({ dataset }: { dataset: Dataset }) {
  return <div className="dataset-actions"><Link className={buttonVariants()} href={datasetSqlUrl(dataset)}>Open in SQL <ArrowUpRight /></Link><Button variant="outline" onClick={async () => { try { await navigator.clipboard.writeText(dataset.sql); toast.success("SQL copied. You can adapt this query.") } catch (error) { toast.error(extractApiError(error)) } }}><Copy />Copy SQL</Button></div>
}

export function DatasetSql({ sql }: { sql: string }) {
  return <SqlEditor value={sql} readOnly />
}

export function DatasetPreview({ dataset, compact = false }: { dataset: Dataset; compact?: boolean }) {
  const query = useDatasetResult(dataset.sql)
  const data = query.data
  const rows = compact ? data?.rows.slice(0, 5) : data?.rows
  function download() {
    if (!data) return
    try {
      const url = URL.createObjectURL(new Blob([analysisCsv(data.columns, data.rows)], { type: "text/csv;charset=utf-8" }))
      const link = document.createElement("a"); link.href = url; link.download = `${dataset.slug}.csv`; link.click(); URL.revokeObjectURL(url)
    } catch (error) { toast.error(extractApiError(error)) }
  }
  return <div className="dataset-preview">
    <div className="dataset-preview-bar"><span>{query.isFetching ? "Reading the current corpus…" : data ? "Returned by SQL" : "Dataset preview"}</span>{!compact && <Button variant="ghost" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw />Refresh</Button>}</div>
    {query.isPending && <><p role="status" className="analysis-note">Loading the result. Collection dates are shown separately from query time.</p><QueryTableLoading /></>}
    {query.error && <Alert variant="destructive"><AlertDescription>{extractApiError(query.error)} The preview could not load; this does not mean the dataset is empty. <Button variant="link" disabled={query.isFetching} onClick={() => void query.refetch()}>Try again</Button></AlertDescription></Alert>}
    {data && <>
      <QueryTable columns={data.columns} types={data.types} rows={rows ?? []} label={dataset.name} />
      {!rows?.length && <p className="analysis-note">No matching rows are available for this query. Check coverage or adjust the SQL.</p>}
      <div className="dataset-preview-bar"><span>{rows?.length} of {data.rows.length} returned rows · {(data.elapsed_ms / 1000).toFixed(2)}s{data.truncated ? " · response truncated" : ""}</span>{!compact && <Button variant="ghost" size="sm" onClick={download}><Download />Export these rows</Button>}</div>
      <p className="analysis-note">{compact ? "Five-row preview. Open the dataset for the returned table, SQL, and collection context." : "The query's filters and LIMIT define this result. CSV includes returned rows only. Source links open today's website; collected_at and last_observed describe the captured evidence."}</p>
      {data.truncated && <p className="analysis-note">The server row or response-size limit was reached. This table and export are partial.</p>}
      {!compact && <p className="analysis-reference">Query {data.query_id} · result received {new Date(query.dataUpdatedAt).toISOString()}</p>}
    </>}
  </div>
}
