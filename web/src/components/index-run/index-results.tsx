import { ExternalLinkIcon } from "lucide-react"
import { useMemo } from "react"

import { IndexEventLog } from "@/components/index-run/index-event-log"
import { IndexResultsTable } from "@/components/index-run/index-results-table"
import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"
import type { IndexLink } from "@/types/index"
import type { ProgressEvent } from "@/types/progress"

type IndexResultsProps = {
  events: ProgressEvent[]
  links: IndexLink[]
}

export function IndexResults({ events, links }: IndexResultsProps) {
  const summary = useMemo(() => {
    const uniqueUrls = new Set(links.map((link) => link.url)).size
    const sourcePages = new Set(links.map((link) => link.source_url)).size
    const internal = links.filter((link) => link.internal).length
    const external = links.length - internal
    const maxDepth = links.reduce(
      (currentMax, link) => Math.max(currentMax, link.depth),
      0
    )

    return {
      external,
      internal,
      maxDepth,
      sourcePages,
      total: links.length,
      uniqueUrls,
    }
  }, [links])

  return (
    <Card
      size="sm"
      className="flex h-[58svh] min-h-[420px] max-h-[720px] flex-col overflow-hidden"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <div className="flex items-center gap-2">
              <Badge variant="secondary">
                <ExternalLinkIcon />
                Results
              </Badge>
              <CardTitle>{summary.total} links</CardTitle>
            </div>
            <CardDescription>
              {summary.uniqueUrls} unique, {summary.internal} internal,{" "}
              {summary.external} external, {summary.sourcePages} sources
            </CardDescription>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs sm:grid-cols-6">
            <ResultStat label="Unique" value={summary.uniqueUrls} />
            <ResultStat label="Internal" value={summary.internal} />
            <ResultStat label="External" value={summary.external} />
            <ResultStat label="Sources" value={summary.sourcePages} />
            <ResultStat label="Depth" value={summary.maxDepth} />
            <ResultStat label="Events" value={events.length} />
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
        <Tabs
          defaultValue="data"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="data">Data</TabsTrigger>
              <TabsTrigger value="log">Log</TabsTrigger>
            </TabsList>
            <div className="text-xs text-muted-foreground">
              {links.length} links, {events.length} events
            </div>
          </div>

          <TabsContent
            value="data"
            className="flex min-h-0 flex-1 flex-col overflow-hidden"
          >
            <IndexResultsTable links={links} />
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

type ResultStatProps = {
  label: string
  value: number
}

function ResultStat({ label, value }: ResultStatProps) {
  return (
    <div className="rounded-md border px-2 py-1.5">
      <div className="text-sm font-medium">{value}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  )
}
