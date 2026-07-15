import { AlertTriangleIcon, ChevronRightIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import type {
  CatalogueQueryMode,
  CatalogueQueryResult,
} from "@/types/catalogue"

type ExplainMode = Exclude<CatalogueQueryMode, "run">

type JsonRecord = Record<string, unknown>

type PlanNode = {
  name: string
  children: PlanNode[]
  details: JsonRecord
  rows: number | null
  rowsScanned: number | null
  seconds: number | null
}

type ParsedExplainPlan = {
  key: string
  formatted: string
  nodes: PlanNode[]
  profile: JsonRecord | null
  error: string | null
}

export function CatalogueExplainPlan({
  result,
  mode,
}: {
  result: CatalogueQueryResult
  mode: ExplainMode
}) {
  const plan = parseExplainPlan(result)
  const analyzed = mode === "explain_analyze"

  return (
    <section className="h-[70svh] max-h-[48rem] min-h-80 min-w-0 overflow-auto rounded-md border bg-background">
      <div className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-3 border-b bg-card/95 px-4 py-3 backdrop-blur">
        <div>
          <div className="text-sm font-medium">
            {analyzed ? "Analyzed query plan" : "Query plan"}
          </div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">
            {analyzed
              ? "The query was executed; operator timings and actual cardinalities are shown."
              : "The query was planned without executing it."}
          </div>
        </div>
        <Badge variant={analyzed ? "secondary" : "outline"}>
          {analyzed ? "Executed" : "Not executed"}
        </Badge>
      </div>

      {plan.error ? (
        <div className="m-4 flex items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-800 dark:text-amber-300">
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
          <div>
            <div className="font-medium">Atlas could not parse this plan</div>
            <div className="mt-1 text-xs">{plan.error}</div>
          </div>
        </div>
      ) : null}

      {analyzed && plan.profile ? <ProfileSummary profile={plan.profile} /> : null}

      {plan.nodes.length > 0 ? (
        <div className="p-4">
          <div className="mb-3 text-[10px] font-medium tracking-wider text-muted-foreground uppercase">
            Operator tree
          </div>
          <div className="space-y-2">
            {plan.nodes.map((node, index) => (
              <PlanTreeNode key={`${node.name}-${index}`} node={node} />
            ))}
          </div>
        </div>
      ) : null}

      <details className="border-t">
        <summary className="cursor-pointer px-4 py-3 text-xs font-medium text-muted-foreground hover:text-foreground">
          Raw {plan.key || "DuckDB"} JSON
        </summary>
        <pre className="overflow-x-auto border-t bg-muted/20 p-4 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
          {plan.formatted}
        </pre>
      </details>
    </section>
  )
}

function ProfileSummary({ profile }: { profile: JsonRecord }) {
  const metrics = [
    ["Latency", formatSeconds(numberValue(profile.latency))],
    ["CPU time", formatSeconds(numberValue(profile.cpu_time))],
    ["Rows scanned", formatCount(numberValue(profile.cumulative_rows_scanned))],
    ["Intermediate rows", formatCount(numberValue(profile.cumulative_cardinality))],
    ["Bytes read", formatBytes(numberValue(profile.total_bytes_read))],
    ["Peak buffer", formatBytes(numberValue(profile.system_peak_buffer_memory))],
  ]

  return (
    <div className="grid gap-px border-b bg-border sm:grid-cols-3 xl:grid-cols-6">
      {metrics.map(([label, value]) => (
        <div key={label} className="bg-background px-4 py-3">
          <div className="text-[10px] tracking-wide text-muted-foreground uppercase">
            {label}
          </div>
          <div className="mt-1 font-mono text-sm font-medium tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  )
}

function PlanTreeNode({ node }: { node: PlanNode }) {
  const estimatedRows = detailValue(node.details, "Estimated Cardinality")

  return (
    <div>
      <div className="rounded-lg border bg-card/70 p-3 shadow-xs">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-[10px]">
            {humanizeOperator(node.name)}
          </Badge>
          {node.rows !== null ? (
            <MetricPill label="rows" value={formatCount(node.rows)} />
          ) : estimatedRows !== null ? (
            <MetricPill label="estimated rows" value={String(estimatedRows)} />
          ) : null}
          {node.rowsScanned !== null ? (
            <MetricPill label="scanned" value={formatCount(node.rowsScanned)} />
          ) : null}
          {node.seconds !== null ? (
            <MetricPill label="time" value={formatSeconds(node.seconds)} />
          ) : null}
        </div>
        {Object.keys(node.details).length > 0 ? (
          <div className="mt-3 grid gap-x-6 gap-y-2 text-xs lg:grid-cols-2">
            {Object.entries(node.details).map(([label, value]) => (
              <PlanDetail key={label} label={label} value={value} />
            ))}
          </div>
        ) : null}
      </div>
      {node.children.length > 0 ? (
        <div className="mt-2 ml-4 space-y-2 border-l pl-4">
          {node.children.map((child, index) => (
            <PlanTreeNode key={`${child.name}-${index}`} node={child} />
          ))}
        </div>
      ) : null}
    </div>
  )
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 font-mono text-[10px] tabular-nums text-muted-foreground">
      <span>{value}</span>
      <span>{label}</span>
    </span>
  )
}

function PlanDetail({ label, value }: { label: string; value: unknown }) {
  if (label === "Projections" && Array.isArray(value) && value.length > 4) {
    return (
      <details className="min-w-0">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          <span className="font-medium text-foreground">{label}</span>
          <span className="ml-2">{value.length.toLocaleString()} columns</span>
        </summary>
        <div className="mt-2 flex flex-wrap gap-1">
          {value.map((item, index) => (
            <code key={`${String(item)}-${index}`} className="rounded bg-muted px-1.5 py-0.5 text-[10px]">
              {String(item)}
            </code>
          ))}
        </div>
      </details>
    )
  }

  return (
    <div className="flex min-w-0 items-start gap-2">
      <span className="shrink-0 font-medium">{label}</span>
      <ChevronRightIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
      <span className="min-w-0 break-words font-mono text-[11px] text-muted-foreground">
        {formatDetail(value)}
      </span>
    </div>
  )
}

function parseExplainPlan(result: CatalogueQueryResult): ParsedExplainPlan {
  const keyIndex = result.columns.indexOf("explain_key")
  const valueIndex = result.columns.indexOf("explain_value")
  const row = result.rows[0]
  const key = keyIndex >= 0 && row ? String(row[keyIndex] ?? "") : ""
  const raw = valueIndex >= 0 && row ? String(row[valueIndex] ?? "") : ""

  if (!raw) {
    return {
      key,
      formatted: "No plan was returned.",
      nodes: [],
      profile: null,
      error: "The Arrow response did not contain an explain_value.",
    }
  }

  try {
    const parsed: unknown = JSON.parse(raw)
    const profile = isRecord(parsed) && !Array.isArray(parsed) ? parsed : null
    const roots = Array.isArray(parsed)
      ? parsed
      : profile
        ? arrayValue(profile.children)
        : []
    const nodes = roots.map(normalizeNode).filter((node): node is PlanNode => node !== null)
    return {
      key,
      formatted: JSON.stringify(parsed, null, 2),
      nodes: unwrapAnalyzeRoot(nodes),
      profile,
      error: null,
    }
  } catch (error) {
    return {
      key,
      formatted: raw,
      nodes: [],
      profile: null,
      error: error instanceof Error ? error.message : "Invalid JSON",
    }
  }
}

function normalizeNode(value: unknown): PlanNode | null {
  if (!isRecord(value)) return null
  const name = stringValue(value.operator_name) ?? stringValue(value.name)
  if (!name) return null
  return {
    name,
    children: arrayValue(value.children)
      .map(normalizeNode)
      .filter((node): node is PlanNode => node !== null),
    details: isRecord(value.extra_info) ? value.extra_info : {},
    rows: numberValue(value.operator_cardinality),
    rowsScanned: numberValue(value.operator_rows_scanned),
    seconds: numberValue(value.operator_timing),
  }
}

function unwrapAnalyzeRoot(nodes: PlanNode[]): PlanNode[] {
  if (nodes.length !== 1) return nodes
  const [root] = nodes
  return root.name === "EXPLAIN_ANALYZE" && root.children.length === 1
    ? root.children
    : nodes
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function arrayValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" ? value : null
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function detailValue(details: JsonRecord, key: string): unknown | null {
  return key in details ? details[key] : null
}

function formatDetail(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ")
  if (isRecord(value)) return JSON.stringify(value)
  return String(value ?? "—")
}

function humanizeOperator(value: string): string {
  return value.replaceAll("_", " ")
}

function formatCount(value: number | null): string {
  return value === null ? "—" : value.toLocaleString()
}

function formatSeconds(value: number | null): string {
  if (value === null) return "—"
  if (value < 0.001) return `${Math.round(value * 1_000_000).toLocaleString()} µs`
  if (value < 1) return `${(value * 1_000).toFixed(1)} ms`
  return `${value.toFixed(2)} s`
}

function formatBytes(value: number | null): string {
  if (value === null) return "—"
  if (value < 1_024) return `${value.toLocaleString()} B`
  const units = ["KiB", "MiB", "GiB", "TiB"]
  let scaled = value
  let unit = -1
  do {
    scaled /= 1_024
    unit += 1
  } while (scaled >= 1_024 && unit < units.length - 1)
  return `${scaled.toFixed(scaled >= 10 ? 1 : 2)} ${units[unit]}`
}
