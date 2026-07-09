import { CheckCircle2Icon, ExternalLinkIcon, XCircleIcon } from "lucide-react"
import { useMemo } from "react"

import { IndexEventLog } from "@/components/index-run/index-event-log"
import { QualityWarnings } from "@/components/quality-warnings"
import { ResultLinkContextMenuContent } from "@/components/result-link-actions"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { ExtractOutput } from "@/types/extract"
import type { CrawlProgressEvent } from "@/types/index"

type ExtractResultsProps = {
  events: CrawlProgressEvent[]
  result: ExtractOutput
}

export function ExtractResults({ events, result }: ExtractResultsProps) {
  const dataColumns = useMemo(() => {
    const seen = new Set<string>()
    for (const row of result.results) {
      for (const key of Object.keys(row)) {
        seen.add(key)
      }
    }
    return Array.from(seen)
  }, [result.results])
  const queryParamCount = result.query_params?.params.length ?? 0

  return (
    <Card
      size="sm"
      className="flex h-[58svh] max-h-[720px] min-h-[420px] flex-col overflow-hidden"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <div className="flex items-center gap-2">
              <Badge variant={result.success ? "secondary" : "destructive"}>
                {result.success ? <CheckCircle2Icon /> : <XCircleIcon />}
                {result.success ? "Complete" : "Failed"}
              </Badge>
              <CardTitle>
                {result.results.length} records, {queryParamCount} query params
              </CardTitle>
            </div>
            <CardDescription>
              {result.source?.schema_type.toUpperCase() ?? "No"} data schema,{" "}
              {result.warnings.length} warnings, {events.length} events
            </CardDescription>
          </div>

          <ContextMenu>
            <ContextMenuTrigger>
              <Button
                variant="outline"
                nativeButton={false}
                render={
                  <a href={result.url} target="_blank" rel="noreferrer" />
                }
              >
                <ExternalLinkIcon />
                Source page
              </Button>
            </ContextMenuTrigger>
            <ResultLinkContextMenuContent url={result.url} />
          </ContextMenu>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
        {result.error ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {result.error}
          </div>
        ) : null}

        <Tabs
          defaultValue="data"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="data">Data</TabsTrigger>
              <TabsTrigger value="query-params">Query Params</TabsTrigger>
              <TabsTrigger value="warnings">Warnings</TabsTrigger>
              <TabsTrigger value="log">Log</TabsTrigger>
            </TabsList>
            <div className="text-xs text-muted-foreground">
              {result.results.length} records, {result.warnings.length} warnings
            </div>
          </div>

          <TabsContent
            value="data"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            {result.results.length === 0 ? (
              <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
                No records were extracted.
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                <table className="w-full min-w-max text-sm">
                  <thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="w-12 px-3 py-2 font-medium">#</th>
                      {dataColumns.map((column) => (
                        <th key={column} className="px-3 py-2 font-medium">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.results.map((row, index) => (
                      <tr key={index} className="border-t">
                        <td className="px-3 py-2 align-top text-xs text-muted-foreground">
                          {index + 1}
                        </td>
                        {dataColumns.map((column) => (
                          <td
                            key={`${index}-${column}`}
                            className="max-w-80 px-3 py-2 align-top"
                          >
                            {formatTableValue(row[column])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>

          <TabsContent
            value="query-params"
            className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden"
          >
            {result.query_params ? (
              <>
                <div className="min-h-0 flex-1 overflow-auto rounded-md border">
                  <table className="w-full text-sm">
                    <thead className="sticky top-0 bg-card text-left text-xs text-muted-foreground">
                      <tr>
                        <th className="px-3 py-2 font-medium">Key</th>
                        <th className="px-3 py-2 font-medium">Kind</th>
                        <th className="px-3 py-2 font-medium">Values</th>
                        <th className="px-3 py-2 font-medium">Description</th>
                        <th className="px-3 py-2 font-medium">Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.query_params.params.map((param) => (
                        <tr key={param.key} className="border-t">
                          <td className="px-3 py-2 align-top font-medium">
                            {param.key}
                          </td>
                          <td className="px-3 py-2 align-top">
                            <div className="flex flex-wrap gap-1.5">
                              <Badge variant="secondary">{param.kind}</Badge>
                              {param.pagination_role ? (
                                <Badge variant="outline">
                                  {param.pagination_role}
                                </Badge>
                              ) : null}
                            </div>
                          </td>
                          <td className="px-3 py-2 align-top">
                            <div className="flex flex-wrap gap-1.5">
                              {param.values.map((value) => (
                                <Badge
                                  key={`${param.key}-${value.value}-${value.label ?? ""}`}
                                  variant="outline"
                                >
                                  {value.value}
                                </Badge>
                              ))}
                            </div>
                          </td>
                          <td className="px-3 py-2 align-top text-muted-foreground">
                            {param.best_effort_description}
                          </td>
                          <td className="px-3 py-2 align-top">
                            {Math.round(param.confidence * 100)}%
                          </td>
                        </tr>
                      ))}
                      {result.query_params.params.length === 0 ? (
                        <tr>
                          <td
                            className="px-3 py-8 text-center text-sm text-muted-foreground"
                            colSpan={5}
                          >
                            No query parameters found.
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                {result.query_params.warnings.length > 0 ? (
                  <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                    {result.query_params.warnings.join(" ")}
                  </div>
                ) : null}
              </>
            ) : (
              <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
                Query parameter extraction was not enabled.
              </div>
            )}
          </TabsContent>

          <TabsContent
            value="warnings"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <QualityWarnings warnings={result.warnings} />
          </TabsContent>

          <TabsContent
            value="log"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <IndexEventLog events={events} />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

function formatTableValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">-</span>
  }

  if (typeof value === "boolean") {
    return value ? "true" : "false"
  }

  if (typeof value === "number" || typeof value === "string") {
    return <span className="line-clamp-3 break-words">{String(value)}</span>
  }

  return (
    <code className="line-clamp-4 whitespace-pre-wrap break-words rounded bg-muted/45 px-1.5 py-1 font-mono text-xs">
      {JSON.stringify(value)}
    </code>
  )
}
