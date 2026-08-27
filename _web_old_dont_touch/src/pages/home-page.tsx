import {
  ArrowRightIcon,
  CheckIcon,
  ChevronDownIcon,
  CircleStopIcon,
  ClipboardIcon,
  Code2Icon,
  DownloadIcon,
  LoaderCircleIcon,
  PlayIcon,
  SearchIcon,
  TriangleAlertIcon,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { flushSync } from "react-dom"
import Markdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ChatCrawlProgress } from "@/components/chat-crawl-progress"
import {
  CrawlResultDialog,
  type CrawlResultRun,
} from "@/components/crawl-result-dialog"
import {
  Card,
  CardContent,
  CardDescription,
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
import { runAssistantSql, streamAssistantTurn } from "@/lib/assistant"
import { extractApiError } from "@/lib/api"
import { cn } from "@/lib/utils"
import type {
  AssistantActivity,
  AssistantEvent,
  AssistantMessage,
  AssistantQueryResult,
  AssistantSqlResult,
  AssistantTurn,
} from "@/types/assistant"

type SqlRun = {
  status: "running" | "completed" | "failed"
  result?: AssistantSqlResult
  error?: string
}

export function HomePage() {
  const [prompt, setPrompt] = useState("")
  const [turns, setTurns] = useState<AssistantTurn[]>([])
  const activeController = useRef<AbortController | null>(null)
  const conversationFeed = useRef<HTMLDivElement | null>(null)
  const conversationEnd = useRef<HTMLDivElement | null>(null)
  const shouldAutoScroll = useRef(true)
  const hasConversation = turns.length > 0
  const isRunning = turns.some((turn) => turn.status === "running")

  useEffect(() => {
    const feed = conversationFeed.current
    const end = conversationEnd.current
    if (!hasConversation || !feed || !end) {
      return
    }

    const scroller = findScrollContainer(feed)
    let lastScrollTop = scroller.scrollTop
    let frame = 0

    const onScroll = () => {
      const nextScrollTop = scroller.scrollTop
      const distanceFromBottom =
        scroller.scrollHeight - nextScrollTop - scroller.clientHeight
      if (nextScrollTop < lastScrollTop - 2) {
        shouldAutoScroll.current = false
      } else if (distanceFromBottom < 160) {
        shouldAutoScroll.current = true
      }
      lastScrollTop = nextScrollTop
    }

    const scrollToProgress = () => {
      if (!shouldAutoScroll.current) {
        return
      }
      window.cancelAnimationFrame(frame)
      frame = window.requestAnimationFrame(() => {
        end.scrollIntoView({ behavior: "smooth", block: "end" })
      })
    }

    scroller.addEventListener("scroll", onScroll, { passive: true })
    const resizeObserver = new ResizeObserver(scrollToProgress)
    resizeObserver.observe(feed)
    scrollToProgress()

    return () => {
      window.cancelAnimationFrame(frame)
      resizeObserver.disconnect()
      scroller.removeEventListener("scroll", onScroll)
    }
  }, [hasConversation])

  const submit = async (question: string) => {
    const trimmed = question.trim()
    if (!trimmed || isRunning) {
      return
    }

    const id = crypto.randomUUID()
    const controller = new AbortController()
    const context = conversationContext(turns)
    const turn: AssistantTurn = {
      id,
      prompt: trimmed,
      status: "running",
      answer: null,
      suggestions: [],
      activities: [],
      queries: [],
      error: null,
    }

    activeController.current = controller
    const beginTurn = () => {
      setPrompt("")
      setTurns((current) => [...current, turn])
    }
    if (turns.length === 0) {
      await transitionHome(beginTurn)
    } else {
      beginTurn()
    }

    try {
      await streamAssistantTurn(
        trimmed,
        context,
        controller.signal,
        (event) => {
          if (event.type === "response.failed") {
            toast.error(
              extractApiError(
                new Error(
                  event.message ?? "Periplus could not complete the response."
                )
              )
            )
          }
          setTurns((current) =>
            current.map((item) =>
              item.id === id ? applyEvent(item, event) : item
            )
          )
        }
      )
      setTurns((current) =>
        current.map((item) =>
          item.id === id && item.status === "running"
            ? {
                ...item,
                status: "failed",
                error: "Periplus closed the response before returning an answer.",
              }
            : item
        )
      )
    } catch (error) {
      if (controller.signal.aborted) {
        setTurns((current) =>
          current.map((item) =>
            item.id === id
              ? {
                  ...item,
                  status: "failed",
                  error: "Response stopped.",
                }
              : item
          )
        )
      } else {
        const message = extractApiError(error)
        toast.error(message)
        setTurns((current) =>
          current.map((item) =>
            item.id === id
              ? { ...item, status: "failed", error: message }
              : item
          )
        )
      }
    } finally {
      if (activeController.current === controller) {
        activeController.current = null
      }
    }
  }

  if (!hasConversation) {
    return <EmptyHome prompt={prompt} setPrompt={setPrompt} submit={submit} />
  }

  return (
    <div className="periplus-conversation mx-auto flex min-h-[calc(100svh-3.5rem)] w-full max-w-5xl flex-col px-4 pt-5 lg:px-6">
      <div className="periplus-conversation-content flex flex-1 flex-col">
        <div
          ref={conversationFeed}
          className="mx-auto flex w-full max-w-4xl flex-col gap-8"
        >
          {turns.map((turn) => (
            <Turn key={turn.id} turn={turn} />
          ))}
          <div ref={conversationEnd} className="h-px scroll-mb-24" />
        </div>
      </div>
      <div className="sticky bottom-0 z-20 mx-auto mt-auto w-full max-w-4xl bg-background py-4">
        <Composer
          prompt={prompt}
          setPrompt={setPrompt}
          submit={submit}
          isRunning={isRunning}
          stop={() => activeController.current?.abort()}
        />
      </div>
    </div>
  )
}

function EmptyHome({
  prompt,
  setPrompt,
  submit,
}: {
  prompt: string
  setPrompt: (value: string) => void
  submit: (value: string) => void
}) {
  return (
    <div className="flex min-h-[calc(100svh-3.5rem)] w-full items-center justify-center px-5 py-12">
      <div className="periplus-home-intro w-full max-w-3xl text-center">
        <h1 className="text-3xl font-medium tracking-tight text-balance sm:text-4xl">
          The web at your fingertips.
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-sm/relaxed text-muted-foreground sm:text-base/relaxed">
          If you could query the web with SQL, what would you ask it?
        </p>
        <Composer
          hero
          className="mx-auto mt-8 w-full max-w-2xl"
          prompt={prompt}
          setPrompt={setPrompt}
          submit={submit}
        />
      </div>
    </div>
  )
}

function Composer({
  className,
  prompt,
  setPrompt,
  submit,
  isRunning = false,
  stop,
  hero = false,
}: {
  className?: string
  prompt: string
  setPrompt: (value: string) => void
  submit: (value: string) => void
  isRunning?: boolean
  stop?: () => void
  hero?: boolean
}) {
  return (
    <div
      className={cn(
        "periplus-question-bar flex items-center gap-2 rounded-2xl border bg-background p-1.5 shadow-sm",
        hero && "border-foreground/20",
        className
      )}
    >
      <SearchIcon className="ml-2.5 size-4 shrink-0 text-muted-foreground" />
      <Textarea
        autoFocus={hero}
        value={prompt}
        disabled={isRunning}
        rows={1}
        placeholder={
          hero ? "Ask anything of the web…" : "Ask a follow-up question…"
        }
        aria-label="Ask Periplus"
        className="max-h-36 min-h-11 flex-1 resize-none border-0 bg-transparent px-1 py-3 text-sm leading-5 shadow-none focus-visible:ring-0 dark:bg-transparent"
        onChange={(event) => setPrompt(event.target.value)}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault()
            void submit(prompt)
          }
        }}
      />
      {isRunning ? (
        <Button
          className="rounded-xl"
          size="icon-lg"
          variant="outline"
          aria-label="Stop response"
          onClick={stop}
        >
          <CircleStopIcon />
        </Button>
      ) : (
        <Button
          className="rounded-xl"
          size="icon-lg"
          disabled={!prompt.trim()}
          aria-label="Ask the web"
          onClick={() => void submit(prompt)}
        >
          <ArrowRightIcon />
        </Button>
      )}
    </div>
  )
}

function Turn({ turn }: { turn: AssistantTurn }) {
  return (
    <article className="min-w-0">
      <div className="ml-auto max-w-[80%] rounded-2xl rounded-br-md bg-muted px-4 py-3 text-sm">
        {turn.prompt}
      </div>

      <div className="mt-4 min-w-0">
        <div className="min-w-0">
          {turn.activities.length > 0 ? (
            <ToolCallList activities={turn.activities} queries={turn.queries} />
          ) : null}

          {turn.answer ? (
            <div className={cn(turn.activities.length > 0 && "mt-4")}>
              <MarkdownAnswer markdown={turn.answer.markdown} />
            </div>
          ) : turn.error ? (
            <div className="mt-3 flex items-start gap-2 text-sm text-destructive">
              <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
              <span>{turn.error}</span>
            </div>
          ) : (
            <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
              <LoaderCircleIcon className="size-3.5 animate-spin text-link" />
              <span>
                {turn.activities.at(-1)?.label ??
                  "Understanding your question…"}
              </span>
            </div>
          )}

          {turn.suggestions.length > 0 ? (
            <SqlSuggestions suggestions={turn.suggestions} />
          ) : null}
        </div>
      </div>
    </article>
  )
}

function ToolCallList({
  activities,
  queries,
}: {
  activities: AssistantActivity[]
  queries: AssistantQueryResult[]
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {activities.map((activity) => (
        <ToolCallItem
          key={activity.callId}
          activity={activity}
          query={queries.find((item) => item.callId === activity.callId)}
        />
      ))}
    </div>
  )
}

function ToolCallItem({
  activity,
  query,
}: {
  activity: AssistantActivity
  query?: AssistantQueryResult
}) {
  const [open, setOpen] = useState(false)
  const rowCount = query?.rowCount

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div
        className="periplus-tool-call overflow-hidden rounded-lg border bg-muted/25"
        data-state={activity.state}
      >
        <CollapsibleTrigger className="flex h-9 w-full items-center gap-2.5 px-3 text-left">
          <QueryStateIcon state={activity.state} />
          <span className="min-w-0 flex-1 truncate text-[0.6875rem] text-muted-foreground">
            {activity.label}
          </span>
          {rowCount !== null && rowCount !== undefined ? (
            <span className="text-[0.625rem] text-muted-foreground tabular-nums">
              {rowCount.toLocaleString()}
              {query?.truncated ? "+" : ""} rows
            </span>
          ) : null}
          {activity.durationMilliseconds !== null ? (
            <span className="text-[0.625rem] text-muted-foreground tabular-nums">
              {formatDuration(activity.durationMilliseconds)}
            </span>
          ) : null}
          <ChevronDownIcon
            className={cn(
              "size-3 text-muted-foreground transition-transform",
              open && "rotate-180"
            )}
          />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <ToolCallDetail activity={activity} query={query} />
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

function ToolCallDetail({
  activity,
  query,
}: {
  activity: AssistantActivity
  query?: AssistantQueryResult
}) {
  if (!query) {
    return (
      <p className="border-t px-3 py-3 text-[0.6875rem] text-muted-foreground">
        {activity.message ?? toolDetail(activity.kind)}
      </p>
    )
  }

  return (
    <div className="border-t">
      <QueryResultTabs
        sql={query.sql}
        displaySql={query.displaySql}
        state={query.state}
        error={query.message ?? undefined}
        result={
          query.state === "completed"
            ? {
                columns: query.columns,
                types: query.types,
                rows: query.rows,
                truncated: query.truncated,
              }
            : undefined
        }
      />
    </div>
  )
}

function MarkdownAnswer({ markdown }: { markdown: string }) {
  return (
    <div className="assistant-markdown max-w-4xl text-sm/relaxed">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {markdown}
      </Markdown>
    </div>
  )
}

function SqlSuggestions({
  suggestions,
}: {
  suggestions: AssistantTurn["suggestions"]
}) {
  const [runs, setRuns] = useState<Record<string, SqlRun>>({})

  const run = async (sql: string) => {
    setRuns((current) => ({
      ...current,
      [sql]: { status: "running" },
    }))
    try {
      const result = await runAssistantSql(sql)
      setRuns((current) => ({
        ...current,
        [sql]: { status: "completed", result },
      }))
    } catch (error) {
      const message = extractApiError(error)
      toast.error(message)
      setRuns((current) => ({
        ...current,
        [sql]: { status: "failed", error: message },
      }))
    }
  }

  return (
    <section className="mt-5">
      <div className="mb-2 flex items-center gap-2">
        <Code2Icon className="size-3.5 text-muted-foreground" />
        <h2 className="text-xs font-medium">Suggested SQL</h2>
        <Badge variant="outline">{suggestions.length}</Badge>
      </div>
      <div className="grid gap-3">
        {suggestions.map((suggestion) => {
          const sqlRun = runs[suggestion.sql]
          return (
            <Card key={suggestion.sql} size="sm">
              <CardHeader>
                <CardTitle>{suggestion.title}</CardTitle>
                <CardDescription>{suggestion.description}</CardDescription>
              </CardHeader>
              <CardContent>
                {sqlRun ? (
                  <div className="overflow-hidden rounded-md border">
                    <QueryResultTabs
                      sql={suggestion.sql}
                      displaySql={suggestion.display_sql}
                      state={sqlRun.status}
                      error={sqlRun.error}
                      result={sqlRun.result}
                    />
                  </div>
                ) : (
                  <SqlBlock
                    sql={suggestion.sql}
                    displaySql={suggestion.display_sql}
                  />
                )}
                <div className="mt-3 flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={() => void copySql(suggestion.sql)}
                  >
                    <ClipboardIcon data-icon="inline-start" />
                    Copy
                  </Button>
                  <Button
                    disabled={sqlRun?.status === "running"}
                    onClick={() => void run(suggestion.sql)}
                  >
                    {sqlRun?.status === "running" ? (
                      <LoaderCircleIcon
                        className="animate-spin"
                        data-icon="inline-start"
                      />
                    ) : (
                      <PlayIcon data-icon="inline-start" />
                    )}
                    {sqlRun?.status === "completed" ? "Run again" : "Run"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>
    </section>
  )
}

function QueryResultTabs({
  sql,
  displaySql,
  state,
  result,
  error,
}: {
  sql: string
  displaySql: string
  state: "running" | "completed" | "failed"
  result?: AssistantSqlResult
  error?: string
}) {
  return (
    <Tabs defaultValue="data" className="min-w-0">
      <TabsList className="h-9 bg-muted/15 px-1">
        <TabsTrigger value="data" className="h-9 px-3 font-sans">
          Data
        </TabsTrigger>
        <TabsTrigger value="sql" className="h-9 px-3 font-sans">
          SQL
        </TabsTrigger>
      </TabsList>
      <TabsContent value="data" className="min-w-0">
        {state === "running" ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            Running bounded query…
          </p>
        ) : state === "failed" ? (
          <p className="px-3 py-3 text-xs text-destructive">
            {error ?? "Query failed."}
          </p>
        ) : result ? (
          <SqlResultTable {...result} />
        ) : (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            No result was returned.
          </p>
        )}
      </TabsContent>
      <TabsContent value="sql" className="min-w-0">
        <SqlBlock sql={sql} displaySql={displaySql || sql} />
      </TabsContent>
    </Tabs>
  )
}

function SqlBlock({ sql, displaySql }: { sql: string; displaySql: string }) {
  return (
    <div className="relative bg-muted/35">
      <pre className="max-h-64 overflow-auto p-3 pr-10 font-mono text-[0.6875rem]/relaxed">
        <code>{displaySql}</code>
      </pre>
      <Button
        className="absolute top-2 right-2"
        size="icon-sm"
        variant="ghost"
        aria-label="Copy SQL"
        onClick={() => void copySql(sql)}
      >
        <ClipboardIcon />
      </Button>
    </div>
  )
}

function SqlResultTable({
  columns,
  types,
  rows,
  truncated,
}: AssistantSqlResult) {
  const [columnWidths, setColumnWidths] = useState(() =>
    columns.map((column, index) => initialColumnWidth(column, rows, index))
  )
  const [crawlRuns, setCrawlRuns] = useState<CrawlResultRun[]>([])
  const resize = useRef<{
    index: number
    pointerId: number
    startX: number
    startWidth: number
  } | null>(null)

  if (rows.length === 0) {
    return (
      <p className="px-3 py-3 text-xs text-muted-foreground">
        The query returned no rows.
      </p>
    )
  }

  const totalWidth = columnWidths.reduce((total, width) => total + width, 0)
  const resizeColumn = (
    index: number,
    clientX: number,
    startX: number,
    startWidth: number
  ) => {
    setColumnWidths((current) =>
      current.map((width, columnIndex) =>
        columnIndex === index
          ? Math.max(72, Math.min(640, startWidth + clientX - startX))
          : width
      )
    )
  }

  return (
    <div className="max-w-full min-w-0 overflow-hidden">
      <TableActions
        columns={columns}
        rows={rows}
        onCrawlStarted={(run) =>
          setCrawlRuns((current) =>
            current.some(({ runId }) => runId === run.runId)
              ? current
              : [...current, run]
          )
        }
      />
      <Table
        className="table-fixed"
        containerClassName="max-h-80 max-w-full overflow-auto overscroll-contain"
        style={{
          width: `max(100%, ${totalWidth}px)`,
        }}
      >
        <colgroup>
          {columnWidths.map((width, index) => (
            <col key={`${columns[index]}-${index}`} style={{ width }} />
          ))}
        </colgroup>
        <TableHeader className="sticky top-0 z-10 bg-card">
          <TableRow>
            {columns.map((column, index) => (
              <TableHead
                key={`${column}-${index}`}
                className="group/column relative overflow-hidden pr-3"
              >
                <span className="block truncate" title={column}>
                  {column}
                </span>
                <span className="block font-mono text-[0.5625rem] font-normal text-muted-foreground">
                  {types[index]}
                </span>
                <span
                  role="separator"
                  tabIndex={0}
                  aria-label={`Resize ${column} column`}
                  aria-orientation="vertical"
                  aria-valuemin={72}
                  aria-valuemax={640}
                  aria-valuenow={Math.round(columnWidths[index] ?? 160)}
                  className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none after:absolute after:inset-y-2 after:right-0 after:w-px after:bg-border group-hover/column:after:bg-primary hover:after:bg-primary focus-visible:outline-2 focus-visible:outline-primary"
                  onDoubleClick={() =>
                    setColumnWidths((current) =>
                      current.map((width, columnIndex) =>
                        columnIndex === index
                          ? initialColumnWidth(column, rows, index)
                          : width
                      )
                    )
                  }
                  onKeyDown={(event) => {
                    if (
                      event.key !== "ArrowLeft" &&
                      event.key !== "ArrowRight"
                    ) {
                      return
                    }
                    event.preventDefault()
                    resizeColumn(
                      index,
                      (columnWidths[index] ?? 160) +
                        (event.key === "ArrowRight" ? 12 : -12),
                      0,
                      0
                    )
                  }}
                  onPointerDown={(event) => {
                    event.preventDefault()
                    event.currentTarget.setPointerCapture(event.pointerId)
                    resize.current = {
                      index,
                      pointerId: event.pointerId,
                      startX: event.clientX,
                      startWidth: columnWidths[index] ?? 160,
                    }
                  }}
                  onPointerMove={(event) => {
                    const active = resize.current
                    if (
                      !active ||
                      active.index !== index ||
                      active.pointerId !== event.pointerId
                    ) {
                      return
                    }
                    resizeColumn(
                      index,
                      event.clientX,
                      active.startX,
                      active.startWidth
                    )
                  }}
                  onPointerUp={(event) => {
                    if (resize.current?.pointerId === event.pointerId) {
                      resize.current = null
                      event.currentTarget.releasePointerCapture(event.pointerId)
                    }
                  }}
                  onPointerCancel={() => {
                    resize.current = null
                  }}
                  onLostPointerCapture={() => {
                    resize.current = null
                  }}
                />
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, rowIndex) => (
            <TableRow key={rowIndex}>
              {columns.map((column, columnIndex) => (
                <TableCell
                  key={`${column}-${columnIndex}`}
                  className="overflow-hidden p-0"
                >
                  <button
                    type="button"
                    className={cn(
                      "block w-full truncate px-2 py-2 text-left font-mono text-[0.6875rem] whitespace-nowrap hover:bg-muted",
                      row[columnIndex] === null &&
                        "text-muted-foreground italic"
                    )}
                    title={`${formatCell(row[columnIndex])}\n\nClick to copy`}
                    onClick={() => void copyCell(row[columnIndex])}
                  >
                    {formatCell(row[columnIndex])}
                  </button>
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {truncated ? (
        <p className="border-t px-3 py-2 text-[0.625rem] text-muted-foreground">
          Result limited for display.
        </p>
      ) : null}
      {crawlRuns.map((run) => (
        <ChatCrawlProgress
          key={run.runId}
          runId={run.runId}
          planSlug={run.planSlug}
          startUrlCount={run.startUrlCount}
        />
      ))}
    </div>
  )
}

function TableActions({
  columns,
  rows,
  onCrawlStarted,
}: {
  columns: string[]
  rows: unknown[][]
  onCrawlStarted: (run: CrawlResultRun) => void
}) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 border-b bg-muted/20 px-2 py-1.5">
      <span className="shrink-0 px-1 text-[0.625rem] text-muted-foreground tabular-nums">
        {rows.length.toLocaleString()} {rows.length === 1 ? "row" : "rows"}
      </span>
      <div className="flex min-w-0 flex-1 items-center justify-end gap-1 overflow-x-auto">
        <CrawlResultDialog
          columns={columns}
          rows={rows}
          onStarted={onCrawlStarted}
        />
        <Button
          className="shrink-0"
          size="sm"
          variant="ghost"
          title="Copy results as CSV"
          onClick={() => void copyTable(columns, rows, "csv")}
        >
          <ClipboardIcon data-icon="inline-start" />
          CSV
        </Button>
        <Button
          className="shrink-0"
          size="sm"
          variant="ghost"
          title="Copy results as JSON"
          onClick={() => void copyTable(columns, rows, "json")}
        >
          <ClipboardIcon data-icon="inline-start" />
          JSON
        </Button>
        <Button
          className="shrink-0"
          size="sm"
          variant="ghost"
          aria-label="Download results as CSV"
          title="Download CSV"
          onClick={() => downloadTable(columns, rows, "csv")}
        >
          <DownloadIcon data-icon="inline-start" />
          CSV
        </Button>
        <Button
          className="shrink-0"
          size="sm"
          variant="ghost"
          aria-label="Download results as JSON"
          title="Download JSON"
          onClick={() => downloadTable(columns, rows, "json")}
        >
          <DownloadIcon data-icon="inline-start" />
          JSON
        </Button>
      </div>
    </div>
  )
}

function QueryStateIcon({
  state,
}: {
  state: "running" | "completed" | "failed"
}) {
  if (state === "running") {
    return (
      <LoaderCircleIcon className="size-3.5 shrink-0 animate-spin text-link" />
    )
  }
  if (state === "failed") {
    return <TriangleAlertIcon className="size-3.5 shrink-0 text-destructive" />
  }
  return (
    <CheckIcon className="size-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
  )
}

function applyEvent(turn: AssistantTurn, event: AssistantEvent): AssistantTurn {
  if (event.type === "response.completed" && event.response) {
    return {
      ...turn,
      status: "completed",
      answer: event.response,
      suggestions: event.suggestions,
    }
  }

  if (event.type === "response.failed") {
    return {
      ...turn,
      status: "failed",
      error: event.message ?? "Periplus could not complete the response.",
    }
  }

  if (!event.call_id || !event.activity) {
    return turn
  }

  const activities = updateActivities(turn.activities, event)
  const queries =
    event.activity === "query"
      ? updateQueries(turn.queries, event)
      : turn.queries

  return { ...turn, activities, queries }
}

function updateActivities(
  activities: AssistantActivity[],
  event: AssistantEvent
) {
  const state =
    event.type === "tool.started"
      ? "running"
      : event.type === "tool.failed"
        ? "failed"
        : "completed"
  const activity: AssistantActivity = {
    callId: event.call_id!,
    kind: event.activity!,
    label: event.purpose ?? event.tool ?? activityLabel(event.activity!),
    state,
    durationMilliseconds: event.duration_ms,
    message: event.message,
  }
  const index = activities.findIndex((item) => item.callId === activity.callId)
  if (index < 0) {
    return [...activities, activity]
  }
  return activities.map((item, itemIndex) =>
    itemIndex === index ? activity : item
  )
}

function updateQueries(queries: AssistantQueryResult[], event: AssistantEvent) {
  const existing = queries.find((query) => query.callId === event.call_id)
  const query: AssistantQueryResult = {
    callId: event.call_id!,
    purpose: event.purpose ?? existing?.purpose ?? "Query catalogue",
    sql: event.sql ?? existing?.sql ?? "",
    displaySql: event.display_sql ?? existing?.displaySql ?? "",
    state:
      event.type === "tool.started"
        ? "running"
        : event.type === "tool.failed"
          ? "failed"
          : "completed",
    durationMilliseconds:
      event.duration_ms ?? existing?.durationMilliseconds ?? null,
    rowCount: event.row_count ?? existing?.rowCount ?? null,
    truncated: event.truncated ?? existing?.truncated ?? false,
    columns: event.columns ?? existing?.columns ?? [],
    types: event.types ?? existing?.types ?? [],
    rows: event.rows ?? existing?.rows ?? [],
    message: event.message ?? existing?.message ?? null,
  }
  if (!existing) {
    return [...queries, query]
  }
  return queries.map((item) => (item.callId === query.callId ? query : item))
}

function activityLabel(activity: AssistantActivity["kind"]) {
  if (activity === "catalogue") {
    return "Inspecting the catalogue"
  }
  if (activity === "draft") {
    return "Preparing SQL"
  }
  return "Querying the catalogue"
}

function toolDetail(activity: AssistantActivity["kind"]) {
  if (activity === "catalogue") {
    return "Inspected public tables, columns, and macros."
  }
  if (activity === "draft") {
    return "Validated a read-only SQL draft against the catalogue."
  }
  return "Ran a bounded read-only query."
}

function conversationContext(turns: AssistantTurn[]): AssistantMessage[] {
  return turns
    .filter((turn) => turn.answer)
    .flatMap((turn) => [
      { role: "user" as const, content: turn.prompt },
      {
        role: "assistant" as const,
        content: turn.answer!.markdown,
      },
    ])
    .slice(-6)
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) {
    return "NULL"
  }
  if (typeof value === "object") {
    return JSON.stringify(value)
  }
  return String(value)
}

function formatDuration(milliseconds: number) {
  if (milliseconds < 1_000) {
    return `${milliseconds} ms`
  }
  return `${(milliseconds / 1_000).toFixed(1)} s`
}

function initialColumnWidth(
  column: string,
  rows: unknown[][],
  columnIndex: number
) {
  const longest = rows
    .slice(0, 24)
    .reduce(
      (length, row) => Math.max(length, formatCell(row[columnIndex]).length),
      column.length
    )
  return Math.max(112, Math.min(320, longest * 7.25 + 28))
}

function tableText(
  columns: string[],
  rows: unknown[][],
  format: "csv" | "json"
) {
  if (format === "csv") {
    return [
      columns.map(csvCell).join(","),
      ...rows.map((row) => row.map(csvCell).join(",")),
    ].join("\n")
  }

  const keys = uniqueColumnNames(columns)
  return JSON.stringify(
    rows.map((row) =>
      Object.fromEntries(keys.map((column, index) => [column, row[index]]))
    ),
    null,
    2
  )
}

function csvCell(value: unknown) {
  const text = formatCell(value)
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

function uniqueColumnNames(columns: string[]) {
  const seen = new Map<string, number>()
  return columns.map((column) => {
    const count = (seen.get(column) ?? 0) + 1
    seen.set(column, count)
    return count === 1 ? column : `${column}_${count}`
  })
}

async function copyCell(value: unknown) {
  try {
    await navigator.clipboard.writeText(formatCell(value))
    toast.success("Cell copied")
  } catch (error) {
    toast.error(extractApiError(error))
  }
}

async function copyTable(
  columns: string[],
  rows: unknown[][],
  format: "csv" | "json"
) {
  try {
    await navigator.clipboard.writeText(tableText(columns, rows, format))
    toast.success(`${format.toUpperCase()} copied`)
  } catch (error) {
    toast.error(extractApiError(error))
  }
}

function downloadTable(
  columns: string[],
  rows: unknown[][],
  format: "csv" | "json"
) {
  try {
    const blob = new Blob(
      [format === "csv" ? "\ufeff" : "", tableText(columns, rows, format)],
      {
        type: format === "csv" ? "text/csv;charset=utf-8" : "application/json",
      }
    )
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = `periplus-results.${format}`
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
  } catch (error) {
    toast.error(extractApiError(error))
  }
}

async function copySql(sql: string) {
  try {
    await navigator.clipboard.writeText(sql)
    toast.success("SQL copied")
  } catch (error) {
    toast.error(extractApiError(error))
  }
}

async function transitionHome(update: () => void) {
  if (
    typeof document.startViewTransition !== "function" ||
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  ) {
    update()
    return
  }

  const transition = document.startViewTransition(() => {
    flushSync(update)
  })
  await transition.updateCallbackDone
}

function findScrollContainer(element: HTMLElement) {
  let candidate = element.parentElement
  while (candidate) {
    const { overflowY } = window.getComputedStyle(candidate)
    if (overflowY === "auto" || overflowY === "scroll") {
      return candidate
    }
    candidate = candidate.parentElement
  }
  return document.documentElement
}
