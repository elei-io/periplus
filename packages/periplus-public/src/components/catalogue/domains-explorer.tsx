"use client"

import Link from "next/link"
import { FormEvent, useState } from "react"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  SearchIcon,
  TriangleAlertIcon,
} from "lucide-react"

import { useDomains } from "@/hooks/use-domains"
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
} from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

const numberFormatter = new Intl.NumberFormat("en")
const dateFormatter = new Intl.DateTimeFormat("en", {
  dateStyle: "medium",
  timeStyle: "short",
})

export function DomainsExplorer() {
  const [draftSearch, setDraftSearch] = useState("")
  const [search, setSearch] = useState("")
  const [cursorHistory, setCursorHistory] = useState<(string | null)[]>([
    null,
  ])
  const cursor = cursorHistory[cursorHistory.length - 1] ?? null
  const domains = useDomains({ search, cursor })

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSearch(draftSearch.trim().toLowerCase())
    setCursorHistory([null])
  }

  function nextPage() {
    if (!domains.data?.nextCursor) return
    setCursorHistory((history) => [...history, domains.data.nextCursor])
  }

  function previousPage() {
    setCursorHistory((history) => history.slice(0, -1))
  }

  return (
    <div className="space-y-4">
      <form
        className="flex flex-col gap-3 sm:flex-row"
        onSubmit={submitSearch}
      >
        <Input
          aria-label="Search domains"
          type="search"
          placeholder="Search hostname prefixes"
          value={draftSearch}
          onChange={(event) => setDraftSearch(event.target.value)}
        />
        <Button type="submit">
          <SearchIcon data-icon="inline-start" />
          Search
        </Button>
      </form>

      {domains.isError ? (
        <Alert variant="destructive">
          <TriangleAlertIcon />
          <AlertTitle>Domain catalogue unavailable</AlertTitle>
          <AlertDescription>{domains.error.message}</AlertDescription>
          <AlertAction>
            <Button
              size="xs"
              variant="outline"
              onClick={() => void domains.refetch()}
            >
              Retry
            </Button>
          </AlertAction>
        </Alert>
      ) : null}

      <div className="overflow-hidden rounded-lg ring-1 ring-foreground/10">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Hostname</TableHead>
              <TableHead>Observations</TableHead>
              <TableHead>Last observed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {domains.isPending ? <LoadingRows /> : null}
            {!domains.isPending && !domains.isError && !domains.data?.items.length ? (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="h-24 text-center text-muted-foreground"
                >
                  {search
                    ? `No observed hostnames begin with “${search}”.`
                    : "No domain observations are available yet."}
                </TableCell>
              </TableRow>
            ) : null}
            {domains.data?.items.map((domain) => (
              <TableRow key={domain.hostname}>
                <TableCell>
                  <Button
                    className="h-auto p-0"
                    nativeButton={false}
                    variant="link"
                    render={
                      <Link
                        href={`/domains/${encodeURIComponent(domain.hostname)}`}
                      />
                    }
                  >
                    {domain.hostname}
                  </Button>
                </TableCell>
                <TableCell>
                  {numberFormatter.format(domain.observationCount)}
                </TableCell>
                <TableCell>{formatObservedAt(domain.lastObservedAt)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2" aria-live="polite">
          <Badge variant="outline">Alphabetical</Badge>
          <span className="text-xs text-muted-foreground">
            Page {cursorHistory.length}
            {domains.isFetching && !domains.isPending ? " · updating" : ""}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={cursorHistory.length === 1 || domains.isFetching}
            onClick={previousPage}
          >
            <ArrowLeftIcon data-icon="inline-start" />
            Previous
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={!domains.data?.nextCursor || domains.isFetching}
            onClick={nextPage}
          >
            Next
            <ArrowRightIcon data-icon="inline-end" />
          </Button>
        </div>
      </div>
    </div>
  )
}

function LoadingRows() {
  return Array.from({ length: 4 }, (_, index) => (
    <TableRow key={index}>
      <TableCell>
        <Skeleton className="h-4 w-40" />
      </TableCell>
      <TableCell>
        <Skeleton className="h-4 w-16" />
      </TableCell>
      <TableCell>
        <Skeleton className="h-4 w-32" />
      </TableCell>
    </TableRow>
  ))
}

function formatObservedAt(value: string | null): string {
  if (!value) return "No retained observation"
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : dateFormatter.format(date)
}
