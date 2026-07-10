import { useVirtualizer } from "@tanstack/react-virtual"
import { ExternalLinkIcon, LinkIcon } from "lucide-react"
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react"

import { ResultLinkContextMenuContent } from "@/components/result-link-actions"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { Input } from "@/components/ui/input"
import type { IndexLink } from "@/types/index"

type IndexResultsProps = {
  links: IndexLink[]
}

export function IndexResults({ links }: IndexResultsProps) {
  const [query, setQuery] = useState("")
  const deferredQuery = useDeferredValue(query)
  const scrollElementRef = useRef<HTMLDivElement>(null)
  const filteredLinks = useMemo(() => {
    const normalizedQuery = deferredQuery.trim().toLowerCase()

    if (!normalizedQuery) {
      return links
    }

    return links.filter((link) =>
      [link.url, link.source_url, link.text, link.title].some((value) =>
        value.toLowerCase().includes(normalizedQuery)
      )
    )
  }, [deferredQuery, links])
  // TanStack Virtual is intentionally stateful; React Compiler skips this component.
  // eslint-disable-next-line react-hooks/incompatible-library
  const rowVirtualizer = useVirtualizer({
    count: filteredLinks.length,
    estimateSize: () => 78,
    getItemKey: (index) => {
      const link = filteredLinks[index]
      return `${link.source_url}-${link.link_index}`
    },
    getScrollElement: () => scrollElementRef.current,
    overscan: 10,
  })

  useEffect(() => {
    if (filteredLinks.length > 0) rowVirtualizer.scrollToIndex(0)
  }, [deferredQuery, filteredLinks.length, rowVirtualizer])

  return (
    <div className="flex max-h-[58svh] min-h-48 flex-col gap-3 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-1">
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-primary">
            <LinkIcon className="size-3.5" />
          </span>
          <span className="text-sm font-medium">
            {links.length} {links.length === 1 ? "link" : "links"}
          </span>
        </div>
        {query ? (
          <span className="text-xs text-muted-foreground">
            {filteredLinks.length} matching
          </span>
        ) : null}
      </div>

      {links.length > 5 ? (
        <Input
          value={query}
          placeholder="Filter links"
          onChange={(event) => setQuery(event.target.value)}
        />
      ) : null}

      {links.length === 0 ? (
        <div className="grid min-h-36 place-items-center rounded-xl border border-dashed bg-muted/15 px-6 text-center text-sm text-muted-foreground">
          No links were discovered from this page.
        </div>
      ) : filteredLinks.length === 0 ? (
        <div className="grid min-h-36 place-items-center rounded-xl border border-dashed bg-muted/15 px-6 text-center text-sm text-muted-foreground">
          No links match this filter.
        </div>
      ) : (
        <div
          ref={scrollElementRef}
          className="min-h-0 flex-1 overflow-auto overscroll-contain rounded-xl border bg-background/30 p-1.5"
          data-testid="index-links-scroll"
          aria-label={`${filteredLinks.length} discovered links`}
          role="list"
        >
          <div
            className="relative w-full"
            style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const link = filteredLinks[virtualRow.index]

              return (
                <div
                  key={virtualRow.key}
                  className="absolute top-0 left-0 w-full pb-1"
                  style={{
                    height: `${virtualRow.size}px`,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                  aria-posinset={virtualRow.index + 1}
                  aria-setsize={filteredLinks.length}
                  role="listitem"
                >
                  <IndexLinkItem link={link} />
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function IndexLinkItem({ link }: { link: IndexLink }) {
  return (
    <ContextMenu>
      <ContextMenuTrigger>
        <article className="grid h-full gap-1.5 rounded-lg p-2.5 transition-colors hover:bg-muted/40">
          <div className="flex min-w-0 items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="truncate text-sm font-medium">
                {link.text || link.title || link.url}
              </h2>
              <p className="truncate text-xs text-muted-foreground">
                {link.url}
              </p>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              nativeButton={false}
              render={<a href={link.url} target="_blank" rel="noreferrer" />}
            >
              <ExternalLinkIcon />
              <span className="sr-only">Open link</span>
            </Button>
          </div>
          <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
            <span>{link.internal ? "Internal" : "External"}</span>
            <span className="text-border">/</span>
            <span>Depth {link.depth}</span>
            <span className="text-border">/</span>
            <span className="min-w-0 truncate">
              Found on {formatSource(link.source_url)}
            </span>
          </div>
        </article>
      </ContextMenuTrigger>
      <ResultLinkContextMenuContent url={link.url} />
    </ContextMenu>
  )
}

function formatSource(value: string) {
  try {
    return new URL(value).hostname
  } catch {
    return value
  }
}
