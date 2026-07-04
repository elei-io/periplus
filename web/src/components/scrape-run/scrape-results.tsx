import {
  CheckCircle2Icon,
  DatabaseIcon,
  ExternalLinkIcon,
  SparklesIcon,
  XCircleIcon,
} from "lucide-react"
import { useMemo } from "react"

import { IndexEventLog } from "@/components/index-run/index-event-log"
import { QualityWarnings } from "@/components/quality-warnings"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { resultActionHref } from "@/lib/result-actions"
import type { CrawlProgressEvent } from "@/types/index"
import type { ScrapeOutput, ScrapePage } from "@/types/scrape"

type ScrapeResultsProps = {
  events: CrawlProgressEvent[]
  result: ScrapeOutput
}

export function ScrapeResults({ events, result }: ScrapeResultsProps) {
  const page = result.pages[0]

  if (!page) {
    return (
      <Card size="sm">
        <CardContent className="p-8 text-center text-sm text-muted-foreground">
          No scrape page was returned.
        </CardContent>
      </Card>
    )
  }

  return <ScrapePageResults events={events} page={page} result={result} />
}

type ScrapePageResultsProps = {
  events: CrawlProgressEvent[]
  page: ScrapePage
  result: ScrapeOutput
}

function ScrapePageResults({ events, page, result }: ScrapePageResultsProps) {
  const crawlJson = useMemo(() => {
    return JSON.stringify(page.crawl ?? {}, null, 2)
  }, [page.crawl])

  return (
    <Card
      size="sm"
      className="flex h-[58svh] max-h-[720px] min-h-[420px] flex-col overflow-hidden"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <div className="flex items-center gap-2">
              <Badge variant={page.success ? "secondary" : "destructive"}>
                {page.success ? <CheckCircle2Icon /> : <XCircleIcon />}
                {page.success ? "Complete" : "Failed"}
              </Badge>
              <CardTitle>{formatBytes(page.html?.length ?? 0)} HTML</CardTitle>
            </div>
            <CardDescription>
              {result.stats.succeeded} succeeded, {result.stats.failed} failed,{" "}
              {page.status_code ?? "no"} status, {page.warnings.length} warnings
            </CardDescription>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              nativeButton={false}
              render={
                <a href={resultActionHref("/playground/index", page.url)} />
              }
            >
              <DatabaseIcon />
              Index
            </Button>
            <Button
              variant="secondary"
              nativeButton={false}
              render={
                <a href={resultActionHref("/playground/extract", page.url)} />
              }
            >
              <SparklesIcon />
              Extract
            </Button>
            <Button
              variant="outline"
              nativeButton={false}
              render={<a href={page.url} target="_blank" rel="noreferrer" />}
            >
              <ExternalLinkIcon />
              Source page
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
        {page.error ? (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {page.error}
          </div>
        ) : null}

        <Tabs
          defaultValue="html"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="html">HTML</TabsTrigger>
              <TabsTrigger value="crawl">Crawl JSON</TabsTrigger>
              <TabsTrigger value="warnings">Warnings</TabsTrigger>
              <TabsTrigger value="log">Log</TabsTrigger>
            </TabsList>
            <div className="text-xs text-muted-foreground">
              {page.duration_seconds.toFixed(2)}s, {events.length} events
            </div>
          </div>

          <TabsContent
            value="html"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            {page.html ? (
              <pre className="min-h-0 flex-1 overflow-auto rounded-md border bg-muted/25 p-3 font-mono text-xs leading-5">
                {page.html}
              </pre>
            ) : (
              <EmptyTab message="No HTML was returned." />
            )}
          </TabsContent>

          <TabsContent
            value="crawl"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <pre className="min-h-0 flex-1 overflow-auto rounded-md border bg-muted/25 p-3 font-mono text-xs leading-5">
              {crawlJson}
            </pre>
          </TabsContent>

          <TabsContent
            value="warnings"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <QualityWarnings warnings={page.warnings} />
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

type EmptyTabProps = {
  message: string
}

function EmptyTab({ message }: EmptyTabProps) {
  return (
    <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
      {message}
    </div>
  )
}

function formatBytes(bytes: number) {
  if (bytes < 1024) {
    return `${bytes} B`
  }

  const kib = bytes / 1024
  if (kib < 1024) {
    return `${kib.toFixed(1)} KiB`
  }

  return `${(kib / 1024).toFixed(1)} MiB`
}
