import {
  CheckCircle2Icon,
  FilterIcon,
  ListFilterIcon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useQuerySchemas } from "@/hooks/use-history-data"
import { HistoryPagination, HISTORY_PAGE_SIZE } from "@/pages/history/history-pagination"
import type { QuerySchemaFilters, QuerySchemaRecord } from "@/types/history"

const defaultFilters: QuerySchemaFilters = {
  matchPattern: "",
  domain: "",
  schemaType: "all",
  enabled: "all",
  warnings: "all",
}

export function QuerySchemasPage() {
  const [filters, setFilters] = useState<QuerySchemaFilters>(defaultFilters)
  const [offset, setOffset] = useState(0)
  const schemasQuery = useQuerySchemas(filters, {
    limit: HISTORY_PAGE_SIZE,
    offset,
  })
  const schemas = schemasQuery.data?.items ?? []
  const total = schemasQuery.data?.total ?? 0

  const patchFilters = (patch: Partial<QuerySchemaFilters>) => {
    setOffset(0)
    setFilters((current) => ({ ...current, ...patch }))
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <ListFilterIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">Query Schemas</h1>
            <Badge variant="outline">{total}</Badge>
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
        <div className="grid gap-2 xl:grid-cols-[minmax(16rem,1fr)_14rem_9rem_9rem_10rem]">
          <div className="relative">
            <FilterIcon className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              value={filters.matchPattern}
              placeholder="*example.com/search*"
              onChange={(event) => patchFilters({ matchPattern: event.target.value })}
            />
          </div>
          <Input
            value={filters.domain}
            placeholder="example.com"
            onChange={(event) => patchFilters({ domain: event.target.value })}
          />
          <FilterSelect
            value={filters.schemaType}
            options={[
              { value: "all", label: "All types" },
              { value: "css", label: "CSS" },
              { value: "xpath", label: "XPath" },
            ]}
            onChange={(schemaType) =>
              patchFilters({ schemaType: schemaType as QuerySchemaFilters["schemaType"] })
            }
            aria-label="Schema type"
          />
          <FilterSelect
            value={filters.enabled}
            options={[
              { value: "all", label: "All states" },
              { value: "enabled", label: "Enabled" },
              { value: "disabled", label: "Disabled" },
            ]}
            onChange={(enabled) =>
              patchFilters({ enabled: enabled as QuerySchemaFilters["enabled"] })
            }
            aria-label="Enabled state"
          />
          <FilterSelect
            value={filters.warnings}
            options={[
              { value: "all", label: "All warnings" },
              { value: "clean", label: "Clean" },
              { value: "warning", label: "Warnings" },
            ]}
            onChange={(warnings) =>
              patchFilters({ warnings: warnings as QuerySchemaFilters["warnings"] })
            }
            aria-label="Warning state"
          />
        </div>
      </section>

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Match</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Params</TableHead>
            <TableHead>Evidence</TableHead>
            <TableHead>Warnings</TableHead>
            <TableHead>Updated</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schemas.map((schema) => (
            <QuerySchemaRow key={schema.id} schema={schema} />
          ))}
          {!schemasQuery.isLoading && schemas.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                No query schemas match.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <HistoryPagination
        total={total}
        limit={schemasQuery.data?.limit ?? HISTORY_PAGE_SIZE}
        offset={schemasQuery.data?.offset ?? offset}
        isFetching={schemasQuery.isFetching}
        onOffsetChange={setOffset}
      />
    </div>
  )
}

function QuerySchemaRow({ schema }: { schema: QuerySchemaRecord }) {
  return (
    <TableRow>
      <TableCell className="max-w-[30rem]">
        <a
          href={`/history/query-schemas/${schema.id}`}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-link underline-offset-4 hover:underline">
            {schema.match}
          </span>
          <span className="block truncate text-muted-foreground">{schema.domain || schema.path || "-"}</span>
        </a>
      </TableCell>
      <TableCell>
        <Badge variant="outline">{schema.schema_type}</Badge>
      </TableCell>
      <TableCell>{schema.param_count}</TableCell>
      <TableCell>{schema.evidence_count}</TableCell>
      <TableCell>
        <Badge variant={schema.warning_count > 0 ? "destructive" : "outline"}>
          {schema.warning_count}
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

function FilterSelect({
  value,
  options,
  onChange,
  "aria-label": ariaLabel,
}: {
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string) => void
  "aria-label": string
}) {
  const selectedLabel = options.find((option) => option.value === value)?.label ?? value

  return (
    <Select
      value={value}
      onValueChange={(nextValue) => {
        if (nextValue !== null) {
          onChange(nextValue)
        }
      }}
    >
      <SelectTrigger aria-label={ariaLabel} className="w-full">
        <span>{selectedLabel}</span>
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
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
