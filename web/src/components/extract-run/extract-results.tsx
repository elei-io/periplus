import { BracesIcon, ExternalLinkIcon } from "lucide-react"
import { useMemo } from "react"

import { QualityWarnings } from "@/components/quality-warnings"
import { ResultLinkContextMenuContent } from "@/components/result-link-actions"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { ExtractInput, ExtractOutput } from "@/types/extract"

type ExtractResultsProps = {
  input: ExtractInput
  result: ExtractOutput
}

export function ExtractResults({ input, result }: ExtractResultsProps) {
  const dataColumns = useMemo(() => {
    const seen = new Set<string>()
    for (const row of result.results) {
      for (const key of Object.keys(row)) seen.add(key)
    }
    return Array.from(seen)
  }, [result.results])
  const queryParamCount = result.query_params?.params.length ?? 0
  const defaultTab = input.extract_data ? "records" : "query-params"
  const resultSummary = [
    input.extract_data
      ? `${result.results.length} ${result.results.length === 1 ? "record" : "records"}`
      : null,
    input.extract_query_params
      ? `${queryParamCount} query ${queryParamCount === 1 ? "param" : "params"}`
      : null,
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <div className="flex max-h-[62svh] min-h-56 flex-col gap-3 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-1">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <BracesIcon className="size-3.5" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium">{resultSummary}</p>
            <p className="truncate text-xs text-muted-foreground">
              {result.url}
            </p>
          </div>
        </div>

        <ContextMenu>
          <ContextMenuTrigger>
            <Button
              size="sm"
              variant="ghost"
              nativeButton={false}
              render={<a href={result.url} target="_blank" rel="noreferrer" />}
            >
              <ExternalLinkIcon />
              Source page
            </Button>
          </ContextMenuTrigger>
          <ResultLinkContextMenuContent url={result.url} />
        </ContextMenu>
      </div>

      {result.error ? (
        <div className="rounded-xl border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {result.error}
        </div>
      ) : null}

      <Tabs
        defaultValue={defaultTab}
        className="flex min-h-0 flex-1 flex-col overflow-hidden"
      >
        <TabsList className="w-fit">
          {input.extract_data ? (
            <TabsTrigger value="records">
              Records
              <Badge variant="secondary">{result.results.length}</Badge>
            </TabsTrigger>
          ) : null}
          {input.extract_query_params ? (
            <TabsTrigger value="query-params">
              Query params
              <Badge variant="secondary">{queryParamCount}</Badge>
            </TabsTrigger>
          ) : null}
          {result.warnings.length > 0 ? (
            <TabsTrigger value="warnings">
              Warnings
              <Badge variant="secondary">{result.warnings.length}</Badge>
            </TabsTrigger>
          ) : null}
        </TabsList>

        {input.extract_data ? (
          <TabsContent
            value="records"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            {result.results.length === 0 ? (
              <EmptyResult>No records were found on this page.</EmptyResult>
            ) : (
              <div className="min-h-0 flex-1 overflow-auto overscroll-contain rounded-xl border bg-background/30">
                <table className="w-full min-w-max text-sm">
                  <thead className="sticky top-0 z-10 bg-card text-left text-xs text-muted-foreground shadow-[0_1px_0_var(--border)]">
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
                      <tr key={index} className="border-t first:border-t-0">
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
        ) : null}

        {input.extract_query_params ? (
          <TabsContent
            value="query-params"
            className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden"
          >
            {result.query_params ? (
              <>
                {result.query_params.params.length === 0 ? (
                  <EmptyResult>
                    No query or pagination controls were found.
                  </EmptyResult>
                ) : (
                  <div className="min-h-0 flex-1 overflow-auto overscroll-contain rounded-xl border bg-background/30">
                    <table className="w-full min-w-[46rem] text-sm">
                      <thead className="sticky top-0 z-10 bg-card text-left text-xs text-muted-foreground shadow-[0_1px_0_var(--border)]">
                        <tr>
                          <th className="px-3 py-2 font-medium">Parameter</th>
                          <th className="px-3 py-2 font-medium">Kind</th>
                          <th className="px-3 py-2 font-medium">Values</th>
                          <th className="px-3 py-2 font-medium">
                            What it controls
                          </th>
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
                              <div className="flex max-w-64 flex-wrap gap-1.5">
                                {param.values.map((value) => (
                                  <Badge
                                    key={`${param.key}-${value.value}-${value.label ?? ""}`}
                                    variant="outline"
                                  >
                                    {value.label ?? value.value}
                                  </Badge>
                                ))}
                              </div>
                            </td>
                            <td className="max-w-72 px-3 py-2 align-top text-muted-foreground">
                              {param.best_effort_description}
                            </td>
                            <td className="px-3 py-2 align-top tabular-nums">
                              {Math.round(param.confidence * 100)}%
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {result.query_params.warnings.length > 0 ? (
                  <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                    {result.query_params.warnings.join(" ")}
                  </div>
                ) : null}
              </>
            ) : (
              <EmptyResult>
                Query parameter discovery was not enabled.
              </EmptyResult>
            )}
          </TabsContent>
        ) : null}

        {result.warnings.length > 0 ? (
          <TabsContent
            value="warnings"
            className="min-h-0 flex-1 overflow-auto overscroll-contain"
          >
            <QualityWarnings warnings={result.warnings} />
          </TabsContent>
        ) : null}
      </Tabs>
    </div>
  )
}

function EmptyResult({ children }: { children: string }) {
  return (
    <div className="grid min-h-36 place-items-center rounded-xl border border-dashed bg-muted/15 px-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function formatTableValue(value: unknown) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-muted-foreground">–</span>
  }
  if (typeof value === "boolean") return value ? "true" : "false"
  if (typeof value === "number" || typeof value === "string") {
    return <span className="line-clamp-3 break-words">{String(value)}</span>
  }
  return (
    <code className="line-clamp-4 rounded bg-muted/45 px-1.5 py-1 font-mono text-xs break-words whitespace-pre-wrap">
      {JSON.stringify(value)}
    </code>
  )
}
