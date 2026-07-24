import { useEffect, useReducer, useRef, useState } from "react"
import {
  ArrowUpRightIcon,
  BotIcon,
  CheckIcon,
  ChevronRightIcon,
  CircleStopIcon,
  CopyIcon,
  DatabaseIcon,
  LoaderCircleIcon,
  SearchIcon,
  SparklesIcon,
  TerminalSquareIcon,
} from "lucide-react"
import { toast } from "sonner"

import { formatSql } from "@/components/catalogue/sql-format"
import { MarkdownContent } from "@/components/markdown-content"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { streamAnalyticsQuestion } from "@/hooks/use-analytics"
import { extractApiError } from "@/lib/api"
import type {
  AnalyticsEvent,
  AnalyticsQuery,
  DirectionState,
} from "@/types/analytics"
import {
  applyAnalyticsEvent,
  initialAnalyticsState,
} from "@/pages/analytics-state"

const EXAMPLE_QUESTIONS = [
  "Which sites have the most retained pages?",
  "Which pages changed between crawls?",
  "Which pages are missing titles or descriptions?",
] as const

const PLANNING_MESSAGES = [
  "Looking for the most useful way into the data…",
  "Finding the angles worth checking…",
  "Turning the question into something measurable…",
] as const

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

function QueryRows({ query }: { query: AnalyticsQuery }) {
  const [expanded, setExpanded] = useState(false)
  if (query.rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
        This successful query returned no rows.
      </div>
    )
  }

  const visibleRows = expanded ? query.rows : query.rows.slice(0, 10)
  return (
    <div className="space-y-3">
      <Table containerClassName="rounded-lg border bg-background/40">
        <TableHeader className="bg-muted/40">
          <TableRow>
            {query.columns.map((column) => (
              <TableHead key={column}>{column.replaceAll("_", " ")}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {visibleRows.map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {row.map((value, columnIndex) => {
                const column = query.columns[columnIndex] ?? String(columnIndex)
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
      {query.rows.length > 10 && (
        <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>
            {expanded
              ? `Showing all ${query.rows.length} returned rows`
              : `Showing 10 of ${query.rows.length} returned rows`}
          </span>
          <Button
            onClick={() => setExpanded((value) => !value)}
            size="sm"
            variant="outline"
          >
            {expanded ? "Show preview" : "Show all"}
          </Button>
        </div>
      )}
    </div>
  )
}

function QueryEvidence({
  index,
  query,
}: {
  index: number
  query: AnalyticsQuery
}) {
  async function copySql() {
    try {
      await navigator.clipboard.writeText(query.sql)
      toast.success("SQL copied.")
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  return (
    <Collapsible className="group/query overflow-hidden rounded-lg border bg-background/40">
      <CollapsibleTrigger className="flex w-full cursor-pointer items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-muted/35 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary">
        <span className="flex min-w-0 flex-1 items-center gap-2">
          <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted">
            <TerminalSquareIcon className="size-3.5 text-muted-foreground" />
          </span>
          <span className="text-sm font-medium">Query {index + 1}</span>
          <Badge variant="secondary">
            {query.rowCount} {query.rowCount === 1 ? "row" : "rows"}
          </Badge>
          {query.truncated && <Badge variant="outline">Truncated</Badge>}
        </span>
        <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-data-open/query:rotate-90 motion-reduce:transition-none" />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t motion-reduce:animate-none data-open:animate-in data-open:fade-in-0">
        <Tabs defaultValue="results">
          <div className="flex items-center justify-between gap-3 px-3">
            <TabsList>
              <TabsTrigger value="results">Results</TabsTrigger>
              <TabsTrigger value="sql">SQL</TabsTrigger>
            </TabsList>
            <div className="flex items-center gap-1">
              <Button onClick={copySql} size="icon-sm" variant="ghost">
                <CopyIcon />
                <span className="sr-only">Copy query {index + 1} SQL</span>
              </Button>
              <Button
                nativeButton={false}
                render={<a href={workbenchHref(query.sql)} />}
                size="icon-sm"
                variant="ghost"
              >
                <ArrowUpRightIcon />
                <span className="sr-only">
                  Open query {index + 1} in Workbench
                </span>
              </Button>
            </div>
          </div>
          <TabsContent className="p-3" value="results">
            <QueryRows query={query} />
          </TabsContent>
          <TabsContent className="p-3" value="sql">
            <pre className="max-h-72 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-xs leading-5 whitespace-pre-wrap">
              {formatSql(query.sql)}
            </pre>
          </TabsContent>
        </Tabs>
      </CollapsibleContent>
    </Collapsible>
  )
}

function OrientationTrail({
  activities,
  queries,
  compact = false,
}: {
  activities: string[]
  queries: AnalyticsQuery[]
  compact?: boolean
}) {
  const visibleActivities = compact ? activities.slice(-4) : activities
  const completedQueries = queries.filter(
    (query) => query.status === "completed"
  )
  const runningQueries = queries.filter((query) => query.status === "running")

  if (activities.length === 0 && queries.length === 0) return null

  return (
    <div className={compact ? "mt-5 space-y-2 pl-11" : "space-y-4"}>
      <div className="space-y-2">
        {visibleActivities.map((activity, index) => (
          <div
            className="flex animate-in items-center gap-2 text-xs text-muted-foreground duration-200 fade-in-0 slide-in-from-bottom-1 motion-reduce:animate-none"
            key={`${activity}-${index}`}
          >
            <span className="flex size-4 shrink-0 items-center justify-center rounded-full bg-emerald-500/10">
              <CheckIcon className="size-2.5 text-emerald-600 dark:text-emerald-400" />
            </span>
            {activity}
          </div>
        ))}
        {runningQueries.map((query) => (
          <div
            className="flex items-center gap-2 text-xs text-muted-foreground"
            key={query.callId}
          >
            <LoaderCircleIcon className="size-4 animate-spin text-primary" />
            Checking the catalogue with read-only SQL…
          </div>
        ))}
      </div>
      {!compact && completedQueries.length > 0 && (
        <div className="space-y-2 border-t pt-4">
          <div>
            <h3 className="text-xs font-medium text-foreground">
              Early catalogue checks
            </h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Read-only SQL Atlas used to decide what was worth exploring.
            </p>
          </div>
          {completedQueries.map((query, index) => (
            <QueryEvidence index={index} key={query.callId} query={query} />
          ))}
        </div>
      )}
    </div>
  )
}

function queryCountLabel(count: number) {
  return `${count} ${count === 1 ? "query" : "queries"}`
}

function answerPreview(answer: string) {
  const plain = answer
    .replace(/[#*_`[\]]/g, "")
    .replace(/\s+/g, " ")
    .trim()
  return plain.length > 150 ? `${plain.slice(0, 147)}…` : plain
}

function directionActivity(
  direction: DirectionState,
  completedQueries: number
) {
  if (direction.status === "waiting") return "Getting oriented"
  if (direction.status === "running") {
    return completedQueries > 0
      ? `Following the evidence · ${queryCountLabel(completedQueries)}`
      : "Looking through the catalogue"
  }
  if (direction.status === "failed") return "Couldn’t complete"
  return queryCountLabel(completedQueries)
}

function DirectionResultCard({
  direction,
  index,
}: {
  direction: DirectionState
  index: number
}) {
  const completedQueries = direction.queries.filter(
    (query) => query.status === "completed"
  )
  const failedQueries = direction.queries.filter(
    (query) => query.status === "failed"
  )
  return (
    <Collapsible
      className="group/analysis animate-in overflow-hidden rounded-xl bg-card shadow-[0_1px_2px_rgb(0_0_0/0.15),0_8px_24px_rgb(0_0_0/0.06)] ring-1 ring-foreground/10 duration-300 fade-in-0 slide-in-from-bottom-1 motion-reduce:animate-none"
      style={{
        animationDelay: `${index * 70}ms`,
        animationFillMode: "both",
      }}
    >
      <CollapsibleTrigger className="flex w-full cursor-pointer items-start gap-3 px-4 py-4 text-left transition-colors hover:bg-muted/25 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary">
        <span className="flex min-w-0 flex-1 items-start gap-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-muted">
            {direction.status === "running" ? (
              <span className="relative flex size-4 items-center justify-center">
                <span className="absolute size-3 animate-ping rounded-full bg-primary/25 motion-reduce:animate-none" />
                <span className="relative size-2 rounded-full bg-primary" />
              </span>
            ) : direction.status === "completed" ? (
              <CheckIcon className="size-4 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <DatabaseIcon className="size-4 text-muted-foreground" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
              <span className="text-sm font-semibold">
                {direction.direction.title}
              </span>
              <span className="text-[11px] text-muted-foreground">
                {directionActivity(direction, completedQueries.length)}
              </span>
            </div>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground">
              {direction.answer
                ? answerPreview(direction.answer)
                : direction.direction.objective}
            </p>
          </div>
        </span>
        <ChevronRightIcon className="mt-2 size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-data-open/analysis:rotate-90 motion-reduce:transition-none" />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t motion-reduce:animate-none data-open:animate-in data-open:fade-in-0">
        <div className="space-y-5 px-4 py-5">
          {direction.answer && (
            <MarkdownContent className="text-foreground/90">
              {direction.answer}
            </MarkdownContent>
          )}
          {direction.status === "waiting" && (
            <p className="text-sm text-muted-foreground">
              Atlas is deciding where to begin.
            </p>
          )}
          {direction.status === "running" && !direction.answer && (
            <p className="text-sm text-muted-foreground">
              Atlas is looking through the catalogue and checking the evidence.
            </p>
          )}
          {direction.error && (
            <p className="text-sm text-destructive">{direction.error}</p>
          )}
          {completedQueries.length > 0 && (
            <Collapsible className="group/evidence overflow-hidden rounded-lg border">
              <CollapsibleTrigger className="flex w-full cursor-pointer items-center justify-between gap-3 px-3 py-2.5 text-left text-sm font-medium transition-colors hover:bg-muted/35 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary">
                <span>
                  Evidence{" "}
                  <span className="font-normal text-muted-foreground">
                    · {queryCountLabel(completedQueries.length)}
                  </span>
                </span>
                <ChevronRightIcon className="size-4 text-muted-foreground transition-transform duration-200 group-data-open/evidence:rotate-90 motion-reduce:transition-none" />
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-2 border-t bg-muted/10 p-2 motion-reduce:animate-none data-open:animate-in data-open:fade-in-0">
                {completedQueries.map((query, queryIndex) => (
                  <QueryEvidence
                    index={queryIndex}
                    key={query.callId}
                    query={query}
                  />
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}
          {failedQueries.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Atlas rejected and retried {failedQueries.length} unsuccessful SQL{" "}
              {failedQueries.length === 1 ? "attempt" : "attempts"} in this
              area.
            </p>
          )}
          <Collapsible className="text-xs text-muted-foreground">
            <CollapsibleTrigger className="cursor-pointer underline-offset-4 hover:text-foreground hover:underline">
              Why Atlas explored this
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 max-w-3xl leading-5">
              {direction.direction.rationale}
            </CollapsibleContent>
          </Collapsible>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function localEvent(
  type: AnalyticsEvent["type"],
  message: string | null = null
): AnalyticsEvent {
  return {
    type,
    run_id: "request",
    plan: null,
    direction: null,
    direction_id: null,
    direction_answer: null,
    scope: null,
    call_id: null,
    message,
    sql: null,
    query_id: null,
    columns: null,
    column_types: null,
    rows: null,
    row_count: null,
    truncated: null,
    delta: null,
    summary: null,
  }
}

export function AnalyticsPage() {
  const [question, setQuestion] = useState("")
  const [state, dispatch] = useReducer(
    applyAnalyticsEvent,
    initialAnalyticsState
  )
  const controller = useRef<AbortController | null>(null)
  const composer = useRef<HTMLTextAreaElement>(null)
  const [planningMessageIndex, setPlanningMessageIndex] = useState(0)

  useEffect(() => {
    if (state.phase !== "planning") return
    const interval = window.setInterval(() => {
      setPlanningMessageIndex(
        (current) => (current + 1) % PLANNING_MESSAGES.length
      )
    }, 2200)
    return () => window.clearInterval(interval)
  }, [state.phase])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const nextQuestion = question.trim()
    if (!nextQuestion || state.running) return

    const nextController = new AbortController()
    controller.current = nextController
    setQuestion(nextQuestion)
    setPlanningMessageIndex(0)
    dispatch(localEvent("analysis.started"))
    try {
      await streamAnalyticsQuestion(
        nextQuestion,
        dispatch,
        nextController.signal
      )
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        const message = extractApiError(error)
        dispatch(localEvent("analysis.failed", message))
        toast.error(message)
      }
    }
  }

  function stop() {
    controller.current?.abort()
    dispatch(localEvent("analysis.failed", "Analysis stopped."))
  }

  const hasResult =
    state.running ||
    state.summary.length > 0 ||
    state.directions.length > 0 ||
    state.error !== null
  const progressMessage =
    state.phase === "planning"
      ? "Finding the useful angles"
      : state.phase === "investigating"
        ? "Following the evidence"
        : state.phase === "synthesizing"
          ? "Bringing the findings together"
          : ""

  return (
    <main className="mx-auto flex min-h-full w-full max-w-6xl flex-col pb-4">
      {!hasResult && (
        <section className="flex min-h-[55svh] flex-col items-center justify-center text-center">
          <div className="flex items-center gap-2 text-xs font-medium tracking-[0.18em] text-primary uppercase">
            <BotIcon className="size-4" /> Ask Atlas
          </div>
          <h1 className="mt-3 text-4xl leading-tight font-semibold tracking-[-0.035em] lg:text-5xl">
            What does the data tell us?
          </h1>
          <p className="mt-3 max-w-2xl text-[15px] leading-6 text-muted-foreground">
            Ask one question about retained catalogue data. Atlas will
            investigate with read-only SQL and show every successful query
            behind its answer.
          </p>
          <div className="mt-7 flex flex-wrap justify-center gap-2">
            <span className="mr-1 flex items-center gap-1.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              <SparklesIcon className="size-3" /> Try an example
            </span>
            {EXAMPLE_QUESTIONS.map((example) => (
              <Button
                className="h-auto rounded-full px-3 py-1.5 font-normal"
                key={example}
                onClick={() => {
                  setQuestion(example)
                  requestAnimationFrame(() => composer.current?.focus())
                }}
                size="sm"
                variant="outline"
              >
                {example}
              </Button>
            ))}
          </div>
        </section>
      )}

      <form
        className={
          hasResult
            ? "sticky top-0 z-20 mb-8 bg-linear-to-b from-background via-background/96 to-transparent py-3"
            : "mt-0"
        }
        onSubmit={submit}
      >
        <div className="relative isolate overflow-hidden rounded-[1.75rem] border border-transparent bg-card shadow-[0_4px_24px_rgb(0_0_0/0.18)] ring-1 ring-foreground/10 transition-[border-color,box-shadow] focus-within:border-primary/35 focus-within:ring-4 focus-within:ring-primary/10">
          <SearchIcon className="pointer-events-none absolute top-[1.1rem] left-4 size-4 text-muted-foreground" />
          <Textarea
            aria-label="Ask Atlas about retained data"
            autoFocus
            className="max-h-40 min-h-13 resize-none overflow-y-auto rounded-[inherit] border-0! bg-transparent py-3.5 pr-32 pl-11 text-sm leading-6 shadow-none focus-visible:border-transparent! focus-visible:ring-0!"
            disabled={state.running}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing
              ) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
            placeholder="Ask a question about retained catalogue data…"
            ref={composer}
            rows={1}
            value={question}
          />
          <div className="absolute right-2 bottom-2">
            {state.running ? (
              <Button
                className="h-9 rounded-full px-4"
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
        <p className="mt-2 px-3 text-[10px] text-muted-foreground">
          Questions and results are not saved. Enter to send · Shift+Enter for a
          new line.
        </p>
      </form>

      {hasResult && (
        <section className="space-y-6 pb-6">
          <p className="sr-only" aria-live="polite">
            {progressMessage}
          </p>

          {state.phase === "planning" && (
            <div className="max-w-4xl border-b pb-7">
              <div className="flex items-center gap-3">
                <span className="flex size-8 items-center justify-center rounded-lg bg-primary/12">
                  <LoaderCircleIcon className="size-4 animate-spin text-primary" />
                </span>
                <div>
                  <h1 className="text-lg font-semibold">Finding the signal</h1>
                  <p
                    className="animate-in text-xs text-muted-foreground duration-300 fade-in-0 motion-reduce:animate-none"
                    key={planningMessageIndex}
                  >
                    {PLANNING_MESSAGES[planningMessageIndex]}
                  </p>
                </div>
              </div>
              <OrientationTrail
                activities={state.orientationActivities}
                compact
                queries={state.orientationQueries}
              />
            </div>
          )}

          {state.phase === "investigating" && (
            <div className="max-w-4xl border-b pb-7">
              <div className="flex items-center gap-3">
                <span className="flex size-8 items-center justify-center rounded-lg bg-primary/12">
                  <LoaderCircleIcon className="size-4 animate-spin text-primary" />
                </span>
                <div>
                  <h1 className="text-lg font-semibold">
                    Following the evidence
                  </h1>
                  <p className="text-xs text-muted-foreground">
                    Useful patterns will appear here as Atlas finds them.
                  </p>
                </div>
              </div>
            </div>
          )}

          {(state.summary || state.phase === "synthesizing") && (
            <div className="max-w-4xl space-y-4 border-b pb-7">
              <div className="flex items-center gap-3">
                <span className="flex size-8 items-center justify-center rounded-lg bg-primary/12">
                  {state.phase === "synthesizing" ? (
                    <LoaderCircleIcon className="size-4 animate-spin text-primary" />
                  ) : (
                    <SparklesIcon className="size-4 text-primary" />
                  )}
                </span>
                <div>
                  <h1 className="text-lg font-semibold">
                    {state.summary
                      ? "What Atlas found"
                      : "Bringing it together"}
                  </h1>
                  <p className="text-xs text-muted-foreground">
                    {state.summary
                      ? "A concise answer grounded in the catalogue."
                      : "Connecting the strongest findings and caveats…"}
                  </p>
                </div>
              </div>
              {state.summary && (
                <MarkdownContent className="text-foreground/90">
                  {state.summary}
                </MarkdownContent>
              )}
            </div>
          )}

          {state.error && (
            <p className="max-w-4xl text-sm text-destructive">{state.error}</p>
          )}

          {state.directions.length > 0 && (
            <div className="space-y-4">
              <div>
                <h2 className="text-base font-semibold">
                  Explore the analysis
                </h2>
                <p className="text-xs text-muted-foreground">
                  Open any area to see its finding and the SQL evidence behind
                  it.
                </p>
              </div>
              {state.plan && (
                <Collapsible className="group/reading max-w-4xl text-xs text-muted-foreground">
                  <CollapsibleTrigger className="flex cursor-pointer items-center gap-2 underline-offset-4 hover:text-foreground hover:underline">
                    <ChevronRightIcon className="size-3.5 transition-transform duration-200 group-data-open/reading:rotate-90 motion-reduce:transition-none" />
                    How Atlas read your question
                    <Badge variant="outline">{state.plan.category}</Badge>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="mt-3 max-w-3xl space-y-4 pl-5 leading-5">
                    <p>{state.plan.interpretation}</p>
                    <OrientationTrail
                      activities={state.orientationActivities}
                      queries={state.orientationQueries}
                    />
                  </CollapsibleContent>
                </Collapsible>
              )}
              {state.directions.map((direction, index) => (
                <DirectionResultCard
                  direction={direction}
                  index={index}
                  key={direction.direction.id}
                />
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  )
}
