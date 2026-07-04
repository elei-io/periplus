import { ExternalLinkIcon } from "lucide-react"
import { useMemo, useState } from "react"

import { ResultLinkContextMenuContent } from "@/components/result-link-actions"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ContextMenu, ContextMenuTrigger } from "@/components/ui/context-menu"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { IndexLink, IndexResultScope } from "@/types/index"

type IndexResultsTableProps = {
  links: IndexLink[]
}

export function IndexResultsTable({ links }: IndexResultsTableProps) {
  const [query, setQuery] = useState("")
  const [scope, setScope] = useState<IndexResultScope>("all")

  const filteredLinks = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()

    return links.filter((link) => {
      if (scope === "internal" && !link.internal) {
        return false
      }

      if (scope === "external" && link.internal) {
        return false
      }

      if (!normalizedQuery) {
        return true
      }

      return [link.url, link.source_url, link.text, link.title].some((value) =>
        value.toLowerCase().includes(normalizedQuery)
      )
    })
  }, [links, query, scope])

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-hidden">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <Input
          value={query}
          placeholder="Filter results"
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="grid grid-cols-3 gap-2 sm:w-72">
          <Button
            type="button"
            variant={scope === "all" ? "default" : "outline"}
            onClick={() => setScope("all")}
          >
            All
          </Button>
          <Button
            type="button"
            variant={scope === "internal" ? "default" : "outline"}
            onClick={() => setScope("internal")}
          >
            Internal
          </Button>
          <Button
            type="button"
            variant={scope === "external" ? "default" : "outline"}
            onClick={() => setScope("external")}
          >
            External
          </Button>
        </div>
      </div>

      {links.length === 0 ? (
        <div className="rounded-md border border-dashed p-8 text-center text-sm text-muted-foreground">
          No links were returned for this index run.
        </div>
      ) : (
        <Table containerClassName="min-h-0 flex-1 overflow-auto rounded-md border">
          <TableHeader>
            <TableRow>
              <TableHead>URL</TableHead>
              <TableHead>Source</TableHead>
              <TableHead className="w-20">Depth</TableHead>
              <TableHead className="w-24">Scope</TableHead>
              <TableHead className="w-16" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredLinks.map((link) => (
              <ContextMenu key={`${link.source_url}-${link.link_index}`}>
                <ContextMenuTrigger render={<TableRow />}>
                  <TableCell>
                    <div className="grid max-w-xl gap-1">
                      <span className="truncate font-medium">
                        {link.text || link.title || link.url}
                      </span>
                      <span className="truncate text-xs text-muted-foreground">
                        {link.url}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <span className="block max-w-xs truncate text-xs text-muted-foreground">
                      {link.source_url}
                    </span>
                  </TableCell>
                  <TableCell>{link.depth}</TableCell>
                  <TableCell>
                    <Badge variant={link.internal ? "default" : "outline"}>
                      {link.internal ? "internal" : "external"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      nativeButton={false}
                      render={
                        <a href={link.url} target="_blank" rel="noreferrer" />
                      }
                    >
                      <ExternalLinkIcon />
                      <span className="sr-only">Open result</span>
                    </Button>
                  </TableCell>
                </ContextMenuTrigger>
                <ResultLinkContextMenuContent url={link.url} />
              </ContextMenu>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}
