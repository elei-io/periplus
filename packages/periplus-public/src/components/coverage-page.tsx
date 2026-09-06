"use client"

import Link from "next/link"
import { RefreshCw, ArrowUpRight } from "lucide-react"
import { useDatasetResult } from "@/hooks/use-dataset-result"
import { coverageSql, datasetSqlUrl } from "@/lib/datasets"
import { displayValue } from "@/lib/query-values"
import { extractApiError } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { QueryTable, QueryValue, QueryTableLoading } from "@/components/query-table"
import { Alert, AlertDescription } from "@/components/ui/alert"

export function CoveragePage() {
  const query = useDatasetResult(coverageSql)
  const rows = query.data?.rows
  return <main className="about-page coverage-overview">
    <header className="about-hero"><span className="eyebrow">Coverage / The current corpus</span><h1>Current coverage.</h1><p>One shared model, with coverage you can inspect. See which sites are represented today, how many URLs are included, and when they were observed. Select a site to explore its observations in SQL.</p><div className="about-actions"><Link className="story-link" href="/datasets">Find a dataset to start with <ArrowUpRight /></Link><Link className="story-link" href="/suggest">Suggest coverage <ArrowUpRight /></Link></div></header>
    <div className="dataset-preview">
      <div className="dataset-preview-bar"><span>{query.isFetching ? "Reading coverage…" : "Coverage from the query API"}</span><Button variant="ghost" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCw />Refresh</Button></div>
      {query.isPending && <><p className="analysis-note" role="status">Counting sites, distinct URLs, and observations…</p><QueryTableLoading /></>}
      {query.error && <Alert variant="destructive"><AlertDescription>{extractApiError(query.error)} Coverage could not be loaded. This is not a report of an empty corpus.</AlertDescription></Alert>}
      {rows && <>
        <div className="coverage-totals">{[["Sites", rows[0]?.[6] ?? 0], ["Distinct URLs", rows[0]?.[7] ?? 0], ["Observations", rows[0]?.[8] ?? 0]].map(([label, value]) => <div key={String(label)}><strong>{displayValue(value)}</strong><span>{String(label)}</span></div>)}</div>
        <QueryTable label="Corpus coverage" columns={["Site", "Distinct URLs", "Observations", "Without a date", "First observed", "Last observed"]} types={["", "BIGINT", "BIGINT", "BIGINT", "TIMESTAMPTZ", "TIMESTAMPTZ"]} rows={rows.map(row => row.slice(0, 6))} renderCell={(value, i) => i === 0 ? <Link className="coverage-site" href={datasetSqlUrl({ sql: `SELECT requested_url, observed_at, outcome, content_id\nFROM web.observation\nWHERE split_part(requested_url, '/', 3) = '${String(value).replaceAll("'", "''")}'\nORDER BY observed_at DESC NULLS LAST\nLIMIT 100;` })}>{String(value)}<ArrowUpRight aria-hidden="true" /></Link> : value === null ? <span className="data-null">Not recorded</span> : <QueryValue value={value} type={i > 3 ? "TIMESTAMPTZ" : "BIGINT"} />} />
        {!rows.length && <p className="analysis-note">No observations are currently available. Suggest coverage to help start the corpus.</p>}
        <p className="analysis-note">Showing {rows.length} of {displayValue(rows[0]?.[6] ?? 0)} sites, ordered by distinct URL count. Totals include all sites. Last observed is the newest known observation on that site; other pages may be older. There is no uniform refresh schedule.</p>
        <p className="analysis-reference">Result received {new Date(query.dataUpdatedAt).toISOString()}. Refresh to query again.</p>
      </>}
    </div>
    <section className="dataset-method"><h2>Know what the counts mean.</h2><div className="dataset-grid"><div><h3>A URL can be observed more than once.</h3><p>Coverage follows selected sources and imports, not representative sampling of the web. Distinct URLs counts requested addresses. Observations counts collection events and imported records, including repeats and failures. An observation may have no retained content or collection date.</p></div><div><h3>Pages and structure become available separately.</h3><p>HTML projections are processed separately. A URL can be present here before its elements or links are ready to query. Inspect outcomes and returned rows when assessing a dataset.</p></div></div><div className="about-actions"><Link className="story-link" href={datasetSqlUrl({ sql: coverageSql })}>Inspect the coverage SQL <ArrowUpRight /></Link><Link className="story-link" href="/about#structure">Understand the data model <ArrowUpRight /></Link></div></section>
  </main>
}
