import { ExternalLinkIcon, SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { ResultLinkContextMenuContent } from "@/components/result-link-actions"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { Input } from "@/components/ui/input"
import type { SearchResult } from "@/types/search"

type SearchResultsProps = {
  results: SearchResult[]
}

export function SearchResults({ results }: SearchResultsProps) {
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

  return (
    <div className="flex max-h-[58svh] min-h-48 flex-col gap-3 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-1">
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-primary">
            <SearchIcon className="size-3.5" />
          </span>
          <span className="text-sm font-medium">
            {results.length} {results.length === 1 ? "result" : "results"}
          </span>
        </div>
        {query ? (
          <span className="text-xs text-muted-foreground">
            {filteredResults.length} matching
          </span>
        ) : null}
      </div>

      {results.length > 5 ? (
        <Input
          value={query}
          placeholder="Filter results"
          onChange={(event) => setQuery(event.target.value)}
        />
      ) : null}

      {results.length === 0 ? (
        <div className="grid min-h-36 place-items-center rounded-xl border border-dashed bg-muted/15 px-6 text-center text-sm text-muted-foreground">
          Nothing came back for this search.
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-1 overflow-auto overscroll-contain rounded-xl border bg-background/30 p-1.5">
          {filteredResults.map((result) => (
            <SearchResultItem key={result.url} result={result} />
          ))}
        </div>
      )}
    </div>
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
