import { memo, useEffect, useRef, useState } from "react"
import {
  ArrowUpRightIcon,
  BotIcon,
  BracesIcon,
  CalendarPlusIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleStopIcon,
  Clock3Icon,
  CopyIcon,
  DatabaseIcon,
  LoaderCircleIcon,
  PlayIcon,
  SearchIcon,
  SparklesIcon,
  TablePropertiesIcon,
  TerminalSquareIcon,
} from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { toast } from "sonner"

import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { ScheduleEditorDialog } from "@/components/crawl-graph/schedule-editor-dialog"
import { MarkdownContent } from "@/components/markdown-content"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { streamSearch } from "@/hooks/use-search"
import { useTriggerCrawlGraph } from "@/hooks/use-crawl-graphs"
import { extractApiError } from "@/lib/api"
import type {
  AcquisitionPlan,
  SearchEvent,
  SearchQueryTrace,
  SeedSearchResult,
} from "@/types/search"

type SearchActivity = {
  callId: string
  tool: string
  label: string
  detail: string | null
  sql: string | null
  results: SeedSearchResult[]
  status: "running" | "completed"
}

const EXAMPLE_QUESTIONS = [
  "Which sites have the most retained pages?",
  "Which pages changed between crawls?",
  "Which pages are missing titles or descriptions?",
] as const

function formatElapsed(seconds: number) {
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`
}

function ElapsedTimer() {
  const [seconds, setSeconds] = useState(0)

  useEffect(() => {
    const startedAt = Date.now()
    const timer = window.setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt) / 1_000))
    }, 1_000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <span
      aria-label={`Elapsed time ${formatElapsed(seconds)}`}
      className="flex shrink-0 items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 font-mono text-[10px] text-muted-foreground tabular-nums"
    >
      <Clock3Icon className="size-3" />
      {formatElapsed(seconds)}
    </span>
  )
}

function activityLabel(tool: string, args: Record<string, unknown> | null) {
  if (tool === "list_tables") return "Inspect tables"
  if (tool === "list_views") return "Inspect views"
  if (tool === "list_materializations") return "Inspect materializations"
  if (tool === "list_macros") return "Inspect macros"
  if (tool === "list_graphs") return "Inspect crawl graphs"
  if (tool === "list_schedules") return "Inspect schedules"
  if (tool === "search_web") return "Discover start URLs"
  if (tool === "submit_acquisition_plan") return "Prepare acquisition plan"
  if (tool === "describe_relation") {
    const relation = args?.qualified_name
    return typeof relation === "string"
      ? `Describe ${relation}`
      : "Describe relation"
  }
  if (tool === "query") return "Run catalogue query"
  return tool.replaceAll("_", " ")
}

function activityDetail(tool: string, args: Record<string, unknown> | null) {
  if (!args || tool === "query" || tool === "describe_relation") return null
  return Object.keys(args).length > 0 ? JSON.stringify(args, null, 2) : null
}

function ActivityIcon({ tool }: { tool: string }) {
  if (tool === "query") return <TerminalSquareIcon />
  if (tool === "describe_relation") return <TablePropertiesIcon />
  if (tool === "list_macros") return <BracesIcon />
  return <DatabaseIcon />
}

function LiveActivity({
  activities,
  status,
}: {
  activities: SearchActivity[]
  status: string | null
}) {
  const visible = activities.slice(-6)
  const hidden = activities.length - visible.length

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      className="min-h-[20rem] overflow-hidden rounded-2xl border bg-card/75 shadow-sm"
      exit={{ opacity: 0, scale: 0.985, y: -10 }}
      initial={{ opacity: 0, y: 10 }}
      layout
      transition={{ duration: 0.22, ease: "easeOut" }}
    >
      <div className="flex items-center justify-between gap-4 border-b px-4 py-3.5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="relative flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <DatabaseIcon className="size-3.5" />
            <span className="absolute inset-0 animate-ping rounded-full border border-primary/20" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-medium">Atlas is working</p>
            <p className="truncate text-xs text-muted-foreground">
              {status ?? "Inspecting retained evidence…"}
            </p>
          </div>
        </div>
        <ElapsedTimer />
      </div>
      <div className="h-64 space-y-1 overflow-y-auto p-2">
        {hidden > 0 && (
          <p className="px-3 py-1 text-[10px] text-muted-foreground">
            {hidden} earlier {hidden === 1 ? "step" : "steps"}
          </p>
        )}
        <AnimatePresence initial={false} mode="popLayout">
          {visible.map((activity) => (
            <motion.div
              animate={{ opacity: 1, x: 0, y: 0 }}
              className="rounded-xl border border-transparent px-3 py-2.5 hover:border-border hover:bg-background/60"
              initial={{ opacity: 0, x: -12, y: 4 }}
              key={activity.callId}
              layout
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <div className="flex min-w-0 items-center gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground [&_svg]:size-3.5">
                  <ActivityIcon tool={activity.tool} />
                </span>
                <span className="min-w-0 flex-1 truncate text-xs font-medium">
                  {activity.label}
                </span>
                {activity.status === "running" ? (
                  <LoaderCircleIcon className="size-3.5 shrink-0 animate-spin text-primary" />
                ) : (
                  <CheckIcon className="size-3.5 shrink-0 text-primary" />
                )}
              </div>
              {(activity.sql ||
                activity.detail ||
                activity.results.length > 0) && (
                <details className="group ml-10">
                  <summary className="mt-1.5 w-fit cursor-pointer list-none text-[10px] text-muted-foreground hover:text-foreground">
                    Inspect{" "}
                    {activity.sql
                      ? "SQL"
                      : activity.results.length > 0
                        ? `${activity.results.length} returned URLs`
                        : "arguments"}
                  </summary>
                  {activity.results.length > 0 ? (
                    <SearchResultLinks results={activity.results} />
                  ) : (
                    <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap">
                      {activity.sql ? formatSql(activity.sql) : activity.detail}
                    </pre>
                  )}
                </details>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}

function SearchResultLinks({ results }: { results: SeedSearchResult[] }) {
  return (
    <div className="mt-2 max-h-56 space-y-1 overflow-y-auto rounded-lg border bg-background/55 p-2">
      {results.map((result) => (
        <a
          className="block rounded-md px-2 py-1.5 hover:bg-muted"
          href={result.url}
          key={result.url}
          rel="noreferrer"
          target="_blank"
        >
          <span className="block truncate text-xs font-medium">
            {result.title}
          </span>
          <span className="block truncate font-mono text-[10px] text-muted-foreground">
            {result.url}
          </span>
        </a>
      ))}
    </div>
  )
}

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

function resultUrl(column: string, value: unknown) {
  if (typeof value !== "string" || !column.toLowerCase().includes("url")) {
    return null
  }
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null
  } catch {
    return null
  }
}

function workbenchHref(sql: string) {
  return `/catalogue/workbench?${new URLSearchParams({ sql })}`
}

function QueryRows({ trace }: { trace: SearchQueryTrace }) {
  const [expanded, setExpanded] = useState(false)

  if (trace.status === "failed") {
    return (
      <div className="rounded-lg border border-destructive/25 bg-destructive/5 p-4 text-xs text-destructive">
        <p className="font-medium">Atlas rejected this SQL and retried.</p>
        <p className="mt-1 font-mono leading-5 text-destructive/80">
          {trace.error}
        </p>
      </div>
    )
  }
  if (trace.status === "running") {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        Running query…
      </div>
    )
  }
  if (trace.rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        This query returned no rows.
      </div>
    )
  }
  const visibleRows = expanded ? trace.rows : trace.rows.slice(0, 10)
  const hiddenRows = trace.rows.length - visibleRows.length

  return (
    <div className="space-y-3">
      <Table containerClassName="rounded-lg border bg-background/40">
        <TableHeader className="bg-muted/40">
          <TableRow>
            {trace.columns.map((column) => (
              <TableHead key={column}>{column.replaceAll("_", " ")}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {visibleRows.map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {row.map((value, columnIndex) => {
                const column = trace.columns[columnIndex] ?? String(columnIndex)
                const url = resultUrl(column, value)
                return (
                  <TableCell
                    className="max-w-md overflow-hidden text-ellipsis whitespace-normal"
                    key={`${rowIndex}-${column}`}
                  >
                    {url ? (
                      <a
                        className="text-primary underline-offset-4 hover:underline"
                        href={url}
                        rel="noreferrer"
                        target="_blank"
                      >
                        {displayValue(value)}
                      </a>
                    ) : (
                      displayValue(value)
                    )}
                  </TableCell>
                )
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {trace.rows.length > 10 && (
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>
            {expanded
              ? `Showing all ${trace.rows.length} rows`
              : `Showing 10 of ${trace.rows.length} rows`}
          </span>
          <Button
            onClick={() => setExpanded((value) => !value)}
            size="sm"
            variant="outline"
          >
            {expanded ? "Show preview" : `Show ${hiddenRows} more`}
          </Button>
        </div>
      )}
    </div>
  )
}

function QueryCard({
  trace,
  index,
}: {
  trace: SearchQueryTrace
  index: number
}) {
  async function copySql() {
    try {
      await navigator.clipboard.writeText(trace.sql)
      toast.success("SQL copied.")
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  return (
    <Card className="gap-0" size="sm">
      <CardHeader className="border-b pb-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted">
            <TerminalSquareIcon className="size-3.5 text-muted-foreground" />
          </span>
          <CardTitle className="text-sm">Query {index + 1}</CardTitle>
          <Badge className="ml-1" variant="secondary">
            {trace.status === "running"
              ? "Running"
              : trace.status === "failed"
                ? "Rejected"
                : `${trace.rows.length} rows`}
          </Badge>
        </div>
        <CardAction className="flex items-center gap-1">
          <Button onClick={copySql} size="icon-sm" variant="ghost">
            <CopyIcon />
            <span className="sr-only">Copy SQL</span>
          </Button>
          <Button
            nativeButton={false}
            render={<a href={workbenchHref(trace.sql)} />}
            size="icon-sm"
            variant="ghost"
          >
            <ArrowUpRightIcon />
            <span className="sr-only">Open query {index + 1} in Workbench</span>
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4 pt-3">
        <Collapsible>
          <CollapsibleTrigger className="flex w-full items-center justify-between rounded-lg border bg-muted/30 px-3 py-2.5 text-left font-mono text-xs hover:bg-muted/60">
            <span className="truncate">{trace.sql}</span>
            <ChevronDownIcon className="ml-3 size-4 shrink-0" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-muted/50 p-3 text-xs whitespace-pre-wrap">
              {trace.sql}
            </pre>
          </CollapsibleContent>
        </Collapsible>
        <QueryRows trace={trace} />
      </CardContent>
    </Card>
  )
}

const FinalResultTabs = memo(function FinalResultTabs({
  trace,
}: {
  trace: SearchQueryTrace
}) {
  async function copySql() {
    try {
      await navigator.clipboard.writeText(trace.sql)
      toast.success("SQL copied.")
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  return (
    <Tabs
      className="min-w-0 overflow-hidden rounded-xl border bg-background/55"
      defaultValue="results"
    >
      <div className="flex min-w-0 items-center justify-between gap-3 border-b pr-3">
        <div className="min-w-0 overflow-x-auto">
          <TabsList className="border-0 px-1">
            <TabsTrigger value="results">
              Results
              <Badge className="ml-2" variant="secondary">
                {trace.rows.length}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="sql">SQL</TabsTrigger>
          </TabsList>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button onClick={copySql} size="icon-sm" variant="ghost">
            <CopyIcon />
            <span className="sr-only">Copy final SQL</span>
          </Button>
          <Button
            nativeButton={false}
            render={<a href={workbenchHref(trace.sql)} />}
            size="sm"
            variant="outline"
          >
            Workbench
            <ArrowUpRightIcon />
          </Button>
        </div>
      </div>
      <TabsContent className="space-y-3 p-3" value="results">
        <p className="px-1 text-xs text-muted-foreground">
          Rows supporting the answer
        </p>
        <QueryRows trace={trace} />
      </TabsContent>
      <TabsContent value="sql">
        <SqlEditor
          ariaLabel="Final SQL"
          height="340px"
          readOnly
          value={formatSql(trace.sql)}
        />
      </TabsContent>
    </Tabs>
  )
})

function ToolEvidenceCard({ activity }: { activity: SearchActivity }) {
  return (
    <Card className="gap-0" size="sm">
      <CardHeader className="border-b pb-3">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground [&_svg]:size-3.5">
            <ActivityIcon tool={activity.tool} />
          </span>
          <CardTitle className="text-sm">{activity.label}</CardTitle>
          <Badge className="ml-1" variant="secondary">
            {activity.status === "completed" ? "Completed" : "Running"}
          </Badge>
        </div>
      </CardHeader>
      {(activity.detail || activity.results.length > 0) && (
        <CardContent className="pt-3">
          {activity.detail && (
            <pre className="max-h-48 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap">
              {activity.detail}
            </pre>
          )}
          {activity.results.length > 0 && (
            <SearchResultLinks results={activity.results} />
          )}
        </CardContent>
      )}
    </Card>
  )
}

const EvidenceSection = memo(function EvidenceSection({
  queries,
  activities,
  toolsCompleted,
}: {
  queries: SearchQueryTrace[]
  activities: SearchActivity[]
  toolsCompleted: number
}) {
  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      initial={{ opacity: 0, y: -8 }}
      layout
      transition={{ delay: 0.08, duration: 0.24 }}
    >
      <Collapsible className="overflow-hidden rounded-xl border bg-card/60">
        <CollapsibleTrigger className="group flex w-full items-center justify-between gap-4 px-4 py-4 text-left hover:bg-muted/30">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
              <DatabaseIcon className="size-4 text-muted-foreground" />
            </span>
            <div className="min-w-0">
              <div className="text-sm font-medium">
                How Atlas reached this answer
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {toolsCompleted} tool steps, selected URLs, and exact SQL rows
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge variant="outline">
              {activities.length} {activities.length === 1 ? "step" : "steps"}
            </Badge>
            <ChevronDownIcon className="size-4 text-muted-foreground" />
          </div>
        </CollapsibleTrigger>
        <CollapsibleContent className="border-t">
          <div className="space-y-4 p-4">
            {activities.map((activity) => {
              const queryIndex = queries.findIndex(
                (trace) => trace.callId === activity.callId
              )
              const trace = queryIndex >= 0 ? queries[queryIndex] : null
              return trace ? (
                <QueryCard
                  index={queryIndex}
                  key={activity.callId}
                  trace={trace}
                />
              ) : (
                <ToolEvidenceCard activity={activity} key={activity.callId} />
              )
            })}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </motion.div>
  )
})

function AcquisitionPlanActions({ plan }: { plan: AcquisitionPlan }) {
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [runId, setRunId] = useState<string | null>(null)
  const trigger = useTriggerCrawlGraph(plan.graph_id)

  function runNow() {
    trigger.mutate(plan.start_urls, {
      onSuccess: (submission) => {
        setRunId(submission.run_id)
        toast.success(`Graph run ${submission.run_id} queued.`)
      },
    })
  }

  return (
    <div className="rounded-xl border border-primary/15 bg-background/55 p-4">
      <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div className="min-w-0">
          <p className="text-sm font-medium">Acquisition plan ready</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {plan.graph_slug} · {plan.start_urls.length} start URL
            {plan.start_urls.length === 1 ? "" : "s"} ·{" "}
            {plan.recommended_run_type === "scheduled"
              ? "Recurring acquisition recommended"
              : "One-off discovery recommended"}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {runId ? (
            <Button
              nativeButton={false}
              render={<a href={`/crawls/graphs/${plan.graph_id}`} />}
              size="sm"
              variant="outline"
            >
              View run
              <ArrowUpRightIcon />
            </Button>
          ) : (
            <Button disabled={trigger.isPending} onClick={runNow} size="sm">
              {trigger.isPending ? (
                <LoaderCircleIcon className="animate-spin" />
              ) : (
                <PlayIcon />
              )}
              Run now
            </Button>
          )}
          <Button
            onClick={() => setScheduleOpen(true)}
            size="sm"
            variant="outline"
          >
            <CalendarPlusIcon />
            Schedule
          </Button>
        </div>
      </div>
      {plan.schedule_summary && (
        <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
          {plan.schedule_summary}
        </p>
      )}
      <ScheduleEditorDialog
        graphId={plan.graph_id}
        initialName={`${plan.graph_slug} acquisition`}
        initialUrls={plan.start_urls}
        onOpenChange={setScheduleOpen}
        open={scheduleOpen}
        schedule={null}
      />
    </div>
  )
}

export function SearchPage() {
  const [question, setQuestion] = useState("")
  const [running, setRunning] = useState(false)
  const [status, setStatus] = useState<string | null>(null)
  const [summary, setSummary] = useState("")
  const [queries, setQueries] = useState<SearchQueryTrace[]>([])
  const [activities, setActivities] = useState<SearchActivity[]>([])
  const [toolsCompleted, setToolsCompleted] = useState(0)
  const [acquisitionPlan, setAcquisitionPlan] =
    useState<AcquisitionPlan | null>(null)
  const controller = useRef<AbortController | null>(null)
  const questionRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => () => controller.current?.abort(), [])

  function applyEvent(event: SearchEvent) {
    if (event.type === "agent.status") setStatus(event.message)
    if (event.type === "tool.started" && event.call_id && event.tool) {
      if (event.tool !== "query") {
        setStatus(`${activityLabel(event.tool, event.arguments)}…`)
      }
      setActivities((current) => [
        ...current,
        {
          callId: event.call_id!,
          tool: event.tool!,
          label: activityLabel(event.tool!, event.arguments),
          detail: activityDetail(event.tool!, event.arguments),
          results: [],
          sql:
            event.tool === "query" && typeof event.arguments?.sql === "string"
              ? event.arguments.sql
              : null,
          status: "running",
        },
      ])
    }
    if (event.type === "search.completed" && event.call_id) {
      setActivities((current) =>
        current.map((activity) =>
          activity.callId === event.call_id
            ? {
                ...activity,
                detail: activityDetail("search_web", event.arguments),
                results: event.search_results ?? [],
              }
            : activity
        )
      )
    }
    if (event.type === "tool.completed" && event.call_id) {
      setToolsCompleted((value) => value + 1)
      setActivities((current) =>
        current.map((activity) =>
          activity.callId === event.call_id
            ? { ...activity, status: "completed" }
            : activity
        )
      )
    }
    if (event.type === "query.started" && event.call_id && event.sql) {
      setStatus("Querying retained evidence…")
      setActivities((current) =>
        current.map((activity) =>
          activity.callId === event.call_id
            ? { ...activity, sql: event.sql! }
            : activity
        )
      )
      setQueries((current) => [
        ...current,
        {
          callId: event.call_id!,
          queryId: null,
          sql: event.sql!,
          columns: [],
          columnTypes: [],
          rows: [],
          status: "running",
          error: null,
        },
      ])
    }
    if (event.type === "query.completed" && event.call_id && event.sql) {
      setQueries((current) =>
        current.map((trace) =>
          trace.callId === event.call_id
            ? {
                callId: trace.callId,
                queryId: event.query_id,
                sql: event.sql!,
                columns: event.columns ?? [],
                columnTypes: event.column_types ?? [],
                rows: event.rows ?? [],
                status: "completed",
                error: null,
              }
            : trace
        )
      )
    }
    if (event.type === "query.failed" && event.call_id) {
      setQueries((current) =>
        current.map((trace) =>
          trace.callId === event.call_id
            ? {
                ...trace,
                status: "failed",
                error: event.message ?? "The query failed validation.",
              }
            : trace
        )
      )
    }
    if (event.type === "summary.delta" && event.delta) {
      setStatus("Writing summary…")
      setSummary((current) => current + event.delta)
    }
    if (event.type === "run.completed") {
      setSummary(event.summary ?? "")
      setAcquisitionPlan(event.acquisition_plan)
      setStatus(null)
      setRunning(false)
    }
    if (event.type === "run.failed") {
      setStatus(null)
      setRunning(false)
      toast.error(event.message ?? "The Atlas agent failed.")
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const nextQuestion = question.trim()
    if (!nextQuestion || running) return

    controller.current?.abort()
    controller.current = new AbortController()
    setRunning(true)
    setStatus("Starting Atlas agent…")
    setSummary("")
    setQueries([])
    setActivities([])
    setToolsCompleted(0)
    setAcquisitionPlan(null)
    try {
      await streamSearch(nextQuestion, applyEvent, controller.current.signal)
    } catch (error) {
      if ((error as Error).name === "AbortError") return
      setRunning(false)
      setStatus(null)
      toast.error(extractApiError(error))
    }
  }

  function stop() {
    controller.current?.abort()
    setRunning(false)
    setStatus(null)
  }

  const hasResults = running || summary || queries.length > 0
  const finalQuery = queries.findLast((trace) => trace.status === "completed")
  const isInvestigating = running && !summary

  return (
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-10 pb-20">
      <section className="flex flex-col items-center gap-7 pt-6 lg:pt-12">
        <div className="w-full max-w-3xl text-center">
          <div className="flex items-center gap-2 text-xs font-medium tracking-[0.18em] text-primary uppercase">
            <span className="mx-auto flex items-center gap-2">
              <BotIcon className="size-4" /> Ask Atlas
            </span>
          </div>
          <h1 className="mt-3 text-4xl leading-[1.08] font-semibold tracking-[-0.035em] lg:text-5xl">
            What do you want to know?
          </h1>
          <p className="mx-auto mt-3 max-w-2xl text-[15px] leading-6 text-muted-foreground">
            Ask in plain language. Atlas will inspect the catalogue, run bounded
            read-only SQL, and show the evidence behind its answer.
          </p>
        </div>

        <form className="w-full max-w-3xl space-y-2.5" onSubmit={handleSubmit}>
          <div className="relative isolate overflow-hidden rounded-[1.75rem] border border-transparent bg-card shadow-[0_1px_6px_rgb(0_0_0/0.12)] ring-1 ring-foreground/10 transition-[border-color,box-shadow] duration-200 focus-within:border-primary/35 focus-within:shadow-[0_3px_16px_rgb(0_0_0/0.2),0_0_24px_rgba(56,189,248,0.12)] focus-within:ring-4 focus-within:ring-primary/10 hover:shadow-[0_2px_12px_rgb(0_0_0/0.17)]">
            <SearchIcon className="pointer-events-none absolute top-[1.1rem] left-4 size-4 text-muted-foreground" />
            <Textarea
              aria-label="Ask Atlas"
              autoFocus
              className="max-h-40 min-h-13 resize-none overflow-y-auto rounded-[inherit] border-0! bg-transparent py-3.5 pr-32 pl-11 text-sm leading-6 shadow-none focus-visible:border-transparent! focus-visible:ring-0!"
              disabled={running}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.currentTarget.form?.requestSubmit()
                }
              }}
              placeholder="Which companies appeared most often in pages crawled last week, and what were they mentioned for?"
              ref={questionRef}
              rows={1}
              value={question}
            />
            <div className="absolute right-2 bottom-2 flex h-9 items-center">
              {running ? (
                <Button
                  className="h-9 rounded-full bg-primary px-4 shadow-[0_0_20px_rgba(56,189,248,0.3)] ring-1 ring-primary/35 transition-[transform,box-shadow,background-color] duration-200 hover:-translate-y-px hover:bg-primary hover:shadow-[0_0_28px_rgba(56,189,248,0.48)] disabled:shadow-none"
                  onClick={stop}
                  size="sm"
                  type="button"
                  variant="outline"
                >
                  <CircleStopIcon /> Stop
                </Button>
              ) : (
                <Button
                  className="h-9 rounded-full px-4"
                  disabled={!question.trim()}
                  size="sm"
                  type="submit"
                >
                  Ask Atlas
                </Button>
              )}
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            Press {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+Enter to
            run.
          </p>
        </form>

        {!hasResults && !question.trim() && (
          <motion.div
            animate={{ opacity: 1, y: 0 }}
            className="flex max-w-3xl flex-wrap items-center justify-center gap-2"
            initial={{ opacity: 0, y: 6 }}
            transition={{ delay: 0.12, duration: 0.24 }}
          >
            <span className="mr-1 flex items-center gap-1.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              <SparklesIcon className="size-3" /> Try an example
            </span>
            {EXAMPLE_QUESTIONS.map((example) => (
              <Button
                className="h-auto rounded-full px-3 py-1.5 font-normal"
                key={example}
                onClick={() => {
                  setQuestion(example)
                  requestAnimationFrame(() => questionRef.current?.focus())
                }}
                size="sm"
                type="button"
                variant="outline"
              >
                {example}
              </Button>
            ))}
          </motion.div>
        )}
      </section>

      {hasResults && (
        <section className="space-y-6" aria-live="polite">
          <AnimatePresence mode="popLayout">
            {isInvestigating && (
              <LiveActivity activities={activities} status={status} />
            )}
          </AnimatePresence>

          {summary && (
            <motion.div
              animate={{ opacity: 1, scale: 1, y: 0 }}
              initial={{ opacity: 0, scale: 0.99, y: 14 }}
              layout
              transition={{ duration: 0.28, ease: "easeOut" }}
            >
              <Card className="overflow-hidden border-primary/20 bg-linear-to-br from-card via-card to-primary/6 shadow-xl shadow-black/5">
                <CardHeader className="border-b bg-background/30">
                  <div className="flex items-center gap-3">
                    <span className="flex size-8 items-center justify-center rounded-lg bg-primary/12">
                      <BotIcon className="size-4 text-primary" />
                    </span>
                    <div>
                      <CardTitle className="text-base">Atlas result</CardTitle>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        {running
                          ? "Preparing the result…"
                          : queries.length > 0
                            ? `Grounded in ${queries.length} ${queries.length === 1 ? "query" : "queries"}`
                            : `Prepared from ${toolsCompleted} discovery ${toolsCompleted === 1 ? "step" : "steps"}`}
                      </p>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-5">
                  <div className="min-h-24 rounded-xl border border-primary/10 bg-primary/4 px-4 py-3.5">
                    <MarkdownContent className="text-foreground/90">
                      {summary}
                    </MarkdownContent>
                  </div>

                  {acquisitionPlan && (
                    <AcquisitionPlanActions
                      key={`${acquisitionPlan.graph_id}:${acquisitionPlan.start_urls.join("|")}`}
                      plan={acquisitionPlan}
                    />
                  )}

                  {finalQuery && <FinalResultTabs trace={finalQuery} />}
                </CardContent>
              </Card>
            </motion.div>
          )}

          {summary && activities.length > 0 && (
            <EvidenceSection
              activities={activities}
              queries={queries}
              toolsCompleted={toolsCompleted}
            />
          )}
        </section>
      )}
    </main>
  )
}
