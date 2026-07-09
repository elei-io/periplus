import {
  CheckCircle2Icon,
  FilterIcon,
  RefreshCwIcon,
  RouteIcon,
  XCircleIcon,
} from "lucide-react"
import { useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { usePaginationSchemas } from "@/hooks/use-history-data"
import type { PaginationSchemaRecord } from "@/types/history"

export function PaginationSchemasPage() {
  const [query, setQuery] = useState("")
  const schemasQuery = usePaginationSchemas()
  const schemas = useMemo(() => schemasQuery.data ?? [], [schemasQuery.data])
  const filteredSchemas = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) {
      return schemas
    }

    return schemas.filter((schema) =>
      [
        schema.match,
        schema.domain,
        schema.path,
        schema.item_selector,
        schema.next_button_selector,
        schema.query_param_key,
        schema.query_param_value_template,
      ]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(needle))
    )
  }, [query, schemas])
  const enabledCount = schemas.filter((schema) => schema.enabled).length

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <RouteIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">Pagination Schemas</h1>
            <Badge variant="outline">{schemas.length}</Badge>
            <Badge variant="secondary">{enabledCount} enabled</Badge>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={schemasQuery.isFetching}
            onClick={() => void schemasQuery.refetch()}
          >
            <RefreshCwIcon />
            Refresh
          </Button>
        </div>
        <div className="relative max-w-xl">
          <FilterIcon className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            value={query}
            placeholder="Filter match, selector, or query param..."
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </section>

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Match</TableHead>
            <TableHead>Item Selector</TableHead>
            <TableHead>Next Selector</TableHead>
            <TableHead>Query Template</TableHead>
            <TableHead>Priority</TableHead>
            <TableHead>Failures</TableHead>
            <TableHead>State</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {filteredSchemas.map((schema) => (
            <PaginationSchemaRow key={schema.id} schema={schema} />
          ))}
          {!schemasQuery.isLoading && filteredSchemas.length === 0 ? (
            <TableRow>
              <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                No pagination schemas match.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>
    </div>
  )
}

function PaginationSchemaRow({ schema }: { schema: PaginationSchemaRecord }) {
  return (
    <TableRow>
      <TableCell className="max-w-[22rem]">
        <a
          href={`/history/pagination-schemas/${schema.id}`}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-link underline-offset-4 hover:underline">
            {schema.match}
          </span>
          <span className="block truncate text-muted-foreground">
            {schema.domain || schema.path || "-"}
          </span>
        </a>
      </TableCell>
      <TableCell className="max-w-[18rem]">
        <code className="block truncate rounded bg-muted/50 px-2 py-1">
          {schema.item_selector}
        </code>
      </TableCell>
      <TableCell className="max-w-[18rem]">
        <code className="block truncate rounded bg-muted/50 px-2 py-1">
          {schema.next_button_selector || "-"}
        </code>
      </TableCell>
      <TableCell className="max-w-[18rem]">
        <code className="block truncate rounded bg-muted/50 px-2 py-1">
          {schema.query_param_key}={schema.query_param_value_template}
        </code>
      </TableCell>
      <TableCell>{schema.priority}</TableCell>
      <TableCell>
        <Badge variant={schema.failure_count > 0 ? "destructive" : "outline"}>
          {schema.failure_count}
        </Badge>
      </TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Badge variant={schema.enabled ? "secondary" : "destructive"}>
            {schema.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}
            {schema.enabled ? "Enabled" : "Disabled"}
          </Badge>
          <span className="text-xs text-muted-foreground">{formatDate(schema.updated_at)}</span>
        </div>
      </TableCell>
    </TableRow>
  )
}

function formatDate(value: string | null) {
  if (!value) {
    return "-"
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}
