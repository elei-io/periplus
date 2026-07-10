import {
  CheckCircle2Icon,
  FilterIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
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
import {
  useCrawlPolicies,
  useUpdateCrawlPolicy,
} from "@/hooks/use-resource-data"
import {
  ResourcePagination,
  RESOURCE_PAGE_SIZE,
} from "@/components/resources/resource-pagination"
import type { CrawlPolicyFilters, CrawlPolicyRecord } from "@/types/resources"

const defaultFilters: CrawlPolicyFilters = {
  matchPattern: "",
  enabled: "all",
  template: "",
  mode: "all",
}

export function CrawlPoliciesPage() {
  const [filters, setFilters] = useState<CrawlPolicyFilters>(defaultFilters)
  const [offset, setOffset] = useState(0)
  const policiesQuery = useCrawlPolicies(filters, {
    limit: RESOURCE_PAGE_SIZE,
    offset,
  })
  const policies = policiesQuery.data?.items ?? []
  const total = policiesQuery.data?.total ?? 0

  const patchFilters = (patch: Partial<CrawlPolicyFilters>) => {
    setOffset(0)
    setFilters((current) => ({ ...current, ...patch }))
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-col gap-3 border-b pb-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <ShieldCheckIcon className="size-4 text-muted-foreground" />
            <h1 className="truncate text-lg font-medium">Crawl Policies</h1>
            <Badge variant="outline">{total}</Badge>
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={policiesQuery.isFetching}
            onClick={() => void policiesQuery.refetch()}
          >
            <RefreshCwIcon />
            Refresh
          </Button>
        </div>
        <div className="grid gap-2 xl:grid-cols-[minmax(16rem,1fr)_12rem_10rem_10rem]">
          <div className="relative">
            <FilterIcon className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              value={filters.matchPattern}
              placeholder="https://example.com/*"
              onChange={(event) =>
                patchFilters({ matchPattern: event.target.value })
              }
            />
          </div>
          <Input
            value={filters.template}
            placeholder="static_fast"
            onChange={(event) => patchFilters({ template: event.target.value })}
          />
          <FilterSelect
            value={filters.mode}
            options={[
              { value: "all", label: "All modes" },
              { value: "static", label: "Static" },
              { value: "dynamic", label: "Dynamic" },
              { value: "app", label: "App" },
            ]}
            onChange={(mode) =>
              patchFilters({ mode: mode as CrawlPolicyFilters["mode"] })
            }
            aria-label="Mode"
          />
          <FilterSelect
            value={filters.enabled}
            options={[
              { value: "all", label: "All states" },
              { value: "enabled", label: "Enabled" },
              { value: "disabled", label: "Disabled" },
            ]}
            onChange={(enabled) =>
              patchFilters({
                enabled: enabled as CrawlPolicyFilters["enabled"],
              })
            }
            aria-label="Enabled state"
          />
        </div>
      </section>

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Match</TableHead>
            <TableHead>Template</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Concurrency</TableHead>
            <TableHead>Updated</TableHead>
            <TableHead className="w-28">Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {policies.map((policy) => (
            <CrawlPolicyRow key={policy.id} policy={policy} />
          ))}
          {!policiesQuery.isLoading && policies.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={6}
                className="h-24 text-center text-muted-foreground"
              >
                No crawl policies match.
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <ResourcePagination
        total={total}
        limit={policiesQuery.data?.limit ?? RESOURCE_PAGE_SIZE}
        offset={policiesQuery.data?.offset ?? offset}
        isFetching={policiesQuery.isFetching}
        onOffsetChange={setOffset}
      />
    </div>
  )
}

function CrawlPolicyRow({ policy }: { policy: CrawlPolicyRecord }) {
  const updatePolicy = useUpdateCrawlPolicy(policy.id)

  const invalidate = () => {
    updatePolicy.mutate({ enabled: false })
  }

  return (
    <TableRow>
      <TableCell className="max-w-[32rem]">
        <a
          href={`/settings/crawl-policies/${policy.id}`}
          className="block min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          <span className="block truncate font-medium text-link underline-offset-4 hover:underline">
            {policy.match}
          </span>
          <span className="block truncate font-mono text-xs text-muted-foreground">
            {policy.id}
          </span>
        </a>
      </TableCell>
      <TableCell>
        <Badge variant="outline">{policy.template ?? "-"}</Badge>
      </TableCell>
      <TableCell>
        <Badge variant="secondary">{policy.mode ?? "-"}</Badge>
        <span className="ml-2 text-xs text-muted-foreground">
          {policy.wait ?? ""}
        </span>
      </TableCell>
      <TableCell>{policy.max_concurrency ?? "-"}</TableCell>
      <TableCell>
        <div className="flex items-center gap-2">
          <Badge variant={policy.enabled ? "secondary" : "destructive"}>
            {policy.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}
            {policy.enabled ? "Enabled" : "Disabled"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {formatDate(policy.updated_at)}
          </span>
        </div>
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          variant="outline"
          disabled={!policy.enabled || updatePolicy.isPending}
          onClick={invalidate}
        >
          Invalidate
        </Button>
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
  const selectedLabel =
    options.find((option) => option.value === value)?.label ?? value

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
