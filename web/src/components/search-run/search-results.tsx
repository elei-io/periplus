import { ExternalLinkIcon, SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { IndexEventLog } from "@/components/index-run/index-event-log"
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
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import type { CrawlProgressEvent } from "@/types/index"
import type { SearchResult } from "@/types/search"

type SearchResultsProps = {
  events: CrawlProgressEvent[]
  results: SearchResult[]
}

export function SearchResults({ events, results }: SearchResultsProps) {
  const [query, setQuery] = useState("")
  const filteredResults = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()

    if (!normalizedQuery) {
      return results
    }

    return results.filter((result) =>
      [result.title, result.description, result.url].some((value) =>
        value.toLowerCase().includes(normalizedQuery)
      )
    )
  }, [query, results])

  const sourceCount = useMemo(() => {
    return new Set(
      results.map((result) => {
        try {
          return new URL(result.url).hostname
        } catch {
          return result.url
        }
      })
    ).size
  }, [results])

  return (
    <Card
      size="sm"
      className="flex h-[58svh] max-h-[720px] min-h-[420px] flex-col overflow-hidden"
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="grid gap-1">
            <div className="flex items-center gap-2">
              <Badge variant="secondary">
                <SearchIcon />
                Results
              </Badge>
              <CardTitle>{results.length} results</CardTitle>
            </div>
            <CardDescription>
              {sourceCount} sources, {events.length} events
            </CardDescription>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
            <ResultStat label="Results" value={results.length} />
            <ResultStat label="Sources" value={sourceCount} />
            <ResultStat label="Events" value={events.length} />
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden">
        <Tabs
          defaultValue="results"
          className="flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <TabsList>
              <TabsTrigger value="results">Results</TabsTrigger>
              <TabsTrigger value="log">Log</TabsTrigger>
            </TabsList>
            <div className="text-xs text-muted-foreground">
              {filteredResults.length} matching results
            </div>
          </div>

          <TabsContent
            value="results"
            className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden"
          >
            <Input
              value={query}
              placeholder="Filter results"
              onChange={(event) => setQuery(event.target.value)}
            />
            {results.length === 0 ? (
              <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
                No search results were returned.
              </div>
            ) : (
              <div className="min-h-0 flex-1 space-y-2 overflow-auto rounded-md border p-2">
                {filteredResults.map((result) => (
                  <SearchResultItem key={result.url} result={result} />
                ))}
              </div>
            )}
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

type SearchResultItemProps = {
  result: SearchResult
}

function SearchResultItem({ result }: SearchResultItemProps) {
  return (
    <ContextMenu>
      <ContextMenuTrigger>
        <article className="grid gap-1.5 rounded-md p-2 hover:bg-muted/40">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-medium">{result.title}</h2>
              <p className="truncate text-xs text-muted-foreground">
                {result.url}
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              nativeButton={false}
              render={<a href={result.url} target="_blank" rel="noreferrer" />}
            >
              <ExternalLinkIcon />
              <span className="sr-only">Open result</span>
            </Button>
          </div>
          {result.description ? (
            <p className="line-clamp-2 text-xs leading-5 text-muted-foreground">
              {result.description}
            </p>
          ) : null}
        </article>
      </ContextMenuTrigger>
      <ResultLinkContextMenuContent url={result.url} />
    </ContextMenu>
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
