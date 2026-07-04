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
  const resultJson = useMemo(() => {
    return JSON.stringify(result.results, null, 2)
  }, [result.results])

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
              <CardTitle>{result.results.length} records</CardTitle>
            </div>
            <CardDescription>
              {result.source?.schema_type.toUpperCase() ?? "No"} schema,{" "}
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
              <pre className="min-h-0 flex-1 overflow-auto rounded-md border bg-muted/25 p-3 font-mono text-xs leading-5">
                {resultJson}
              </pre>
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
