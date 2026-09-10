"use client"

import Link from "next/link"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { QueryTable } from "@/components/query-table"
import { analysisView, inspectedEvidence } from "@/lib/analysis-view"
import type { DiscoveryMessage } from "@/types/assistant"

export function DiscoveryFindings({ messages, busy, interrupted }: { messages: DiscoveryMessage[]; busy: boolean; interrupted: boolean }) {
  const views = messages.filter(message => message.role === "assistant").map(analysisView)
  const view = views.at(-1)
  const queries = views.flatMap(view => view.queries)
  const completed = inspectedEvidence(messages)
  return <Card className="dataset-surface min-w-0">
    <CardHeader><CardTitle>Findings</CardTitle><CardDescription>{interrupted ? "The run stopped before completion. Completed evidence is available below." : busy ? "Exploring the corpus. Evidence appears as queries finish." : completed.length ? "Evidence inspected during this conversation. These query results are not a proposed dataset." : view?.finding ? "The answer is in the conversation. Suggested schemas, datasets and coverage appear here when useful." : queries.length ? "Queries were attempted, but no evidence was returned. See the conversation for details." : "Explore a question to find sources, possible datasets and gaps in coverage."}</CardDescription></CardHeader>
    <CardContent className="flex min-w-0 flex-col gap-4">
      {completed.map(({ result, purpose }) => <details className="workspace-disclosure" key={result.query_id}><summary>{purpose} · {result.rows.length} returned rows</summary><p>Snapshot {result.source_snapshot}{result.truncated ? " · Query result truncated" : ""} · Up to five rows shown</p><QueryTable columns={result.columns} types={result.types} rows={result.rows.slice(0, 5)} label={purpose} /><details><summary>SQL</summary><pre className="overflow-auto">{result.sql}</pre><Button variant="ghost" nativeButton={false} render={<Link href={`/sql?${new URLSearchParams({ sql: result.sql })}`} target="_blank" rel="noopener noreferrer" />}>Open in SQL ↗</Button></details></details>)}
      {views.flatMap(view => view.coverage).map((suggestion, index) => <div key={index} className="flex flex-col gap-2"><p>{suggestion.reason}</p><details className="workspace-disclosure"><summary>Suggested coverage</summary><p>{suggestion.description}</p></details><Button className="self-start" variant="outline" nativeButton={false} render={<Link href={`/coverage?${new URLSearchParams({ description: suggestion.description })}#coverage-request`} target="_blank" rel="noopener noreferrer" />}>Review coverage request</Button></div>)}
    </CardContent>
  </Card>
}
