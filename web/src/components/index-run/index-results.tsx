import { ExternalLinkIcon, LinkIcon } from "lucide-react"

import { ResultLinkContextMenuContent } from "@/components/result-link-actions"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import type { IndexLink, IndexOutput } from "@/types/index"

type IndexResultsProps = {
  result?: IndexOutput
}

export function IndexResults({ result }: IndexResultsProps) {
  const links = result?.sample_links ?? []

  return (
    <div className="flex max-h-[58svh] min-h-48 flex-col gap-3 overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-1">
        <div className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-full bg-primary/10 text-primary">
            <LinkIcon className="size-3.5" />
          </span>
          <span className="text-sm font-medium">Result health</span>
        </div>
        <span className="text-xs text-muted-foreground">
          {result?.result_links ?? 0} eligible links
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Pages" value={result?.pages ?? 0} />
        <Metric label="Failed" value={result?.failed_pages ?? 0} />
        <Metric label="Internal" value={result?.internal_links ?? 0} />
        <Metric label="External" value={result?.external_links ?? 0} />
      </div>

      {links.length === 0 ? (
        <div className="grid min-h-36 place-items-center rounded-xl border border-dashed bg-muted/15 px-6 text-center text-sm text-muted-foreground">
          No links were discovered from this page.
        </div>
      ) : (
        <div
          className="min-h-0 flex-1 space-y-1 overflow-auto overscroll-contain rounded-xl border bg-background/30 p-1.5"
          data-testid="index-links-scroll"
          aria-label={`${links.length} sampled links`}
          role="list"
        >
          <p className="px-2 py-1 text-xs text-muted-foreground">
            Random sample of {links.length} links for a quick quality check
          </p>
          {links.map((link) => (
            <div key={`${link.source_url}-${link.link_index}`} role="listitem">
              <IndexLinkItem link={link} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border bg-muted/15 px-3 py-2">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tabular-nums">{value}</p>
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
