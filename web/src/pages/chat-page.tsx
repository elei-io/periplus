import { useEffect, useMemo, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import {
  BotIcon,
  CheckCircle2Icon,
  CircleStopIcon,
  LoaderCircleIcon,
  SearchIcon,
  SparklesIcon,
} from "lucide-react"
import { AnimatePresence, motion } from "motion/react"
import { toast } from "sonner"

import { MarkdownContent } from "@/components/markdown-content"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Textarea } from "@/components/ui/textarea"
import {
  streamChatTurn,
  useApplyChatScheduleChange,
  useChat,
  useCreateChat,
} from "@/hooks/use-search"
import { useGraphRun } from "@/hooks/use-crawl-graphs"
import { extractApiError } from "@/lib/api"
import {
  AcquisitionPlanActions,
  EvidenceSection,
  FinalResultTabs,
  LiveActivity,
  type SearchActivity,
} from "@/pages/search-page"
import type {
  AcquisitionPlan,
  AssistantTurnContent,
  ChatItem,
  ChatQueryArtifact,
  ChatToolArtifact,
  SearchEvent,
  SearchQueryTrace,
  ScheduleChangeProposal,
} from "@/types/search"


const EXAMPLE_QUESTIONS = [
  "What do we know about book prices?",
  "Help me plan acquisition for Japanese electronics marketplaces.",
  "Which sites have the most retained pages?",
] as const


function toolLabel(tool: string) {
  const labels: Record<string, string> = {
    list_catalogue_relations: "Inspect catalogue",
    list_materializations: "Inspect materializations",
    list_macros: "Inspect macros",
    list_crawl_graphs: "Inspect crawl graphs",
    get_crawl_graph: "Inspect graph details",
    list_crawl_schedules: "Inspect schedules",
    get_crawl_schedule: "Inspect schedule details",
    inspect_graph_run: "Inspect graph run",
    search_public_web: "Discover start URLs",
    propose_acquisition: "Prepare acquisition plans",
    propose_schedule_change: "Prepare schedule change",
    describe_relation: "Describe relation",
    query_catalogue: "Run catalogue query",
  }
  return labels[tool] ?? tool.replaceAll("_", " ")
}


function queryTrace(query: ChatQueryArtifact): SearchQueryTrace {
  return {
    callId: query.call_id,
    queryId: query.query_id,
    sql: query.sql,
    columns: query.columns,
    columnTypes: query.column_types,
    rows: query.rows,
    row_count: query.row_count,
    truncated: query.truncated,
    status: query.status,
    error: query.error,
  }
}


function toolActivity(tool: ChatToolArtifact): SearchActivity {
  return {
    callId: tool.call_id,
    tool: tool.tool,
    label: toolLabel(tool.tool),
    detail:
      Object.keys(tool.arguments).length > 0
        ? JSON.stringify(tool.arguments, null, 2)
        : null,
    sql:
      tool.tool === "query_catalogue" && typeof tool.arguments.sql === "string"
        ? tool.arguments.sql
        : null,
    results: tool.search_results,
    result: tool.result_preview,
    status: "completed",
  }
}


function AssistantTurnCard({
  itemId,
  chatId,
  content,
}: {
  itemId: string
  chatId: string
  content: AssistantTurnContent
}) {
  const queries = content.queries.map(queryTrace)
  const activities = content.tools.map(toolActivity)
  const finalQuery = queries.findLast((query) => query.status === "completed")

  return (
    <motion.div
      animate={{ opacity: 1, y: 0 }}
      initial={{ opacity: 0, y: 8 }}
      className="space-y-4"
    >
      <Card className="overflow-hidden border-primary/20 bg-linear-to-br from-card via-card to-primary/6 shadow-xl shadow-black/5">
        <CardHeader className="border-b bg-background/30">
          <div className="flex items-center gap-3">
            <span className="flex size-8 items-center justify-center rounded-lg bg-primary/12">
              <BotIcon className="size-4 text-primary" />
            </span>
            <div>
              <CardTitle className="text-base">Atlas</CardTitle>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {content.failed
                  ? "This turn could not be completed"
                  : queries.length > 0
                    ? `Grounded in ${queries.length} ${queries.length === 1 ? "query" : "queries"}`
                    : activities.length > 0
                      ? `${activities.length} tool ${activities.length === 1 ? "step" : "steps"}`
                      : "Chat response"}
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <MarkdownContent className="text-foreground/90">
            {content.summary}
          </MarkdownContent>
          {content.acquisition_plans.map((plan, index) => (
            <AcquisitionPlanActions
              chatId={chatId}
              itemId={itemId}
              key={`${plan.graph_id}:${index}`}
              plan={plan}
              planIndex={index}
            />
          ))}
          {content.schedule_changes.map((change, index) => (
            <ScheduleChangeActions
              change={change}
              chatId={chatId}
              itemId={itemId}
              key={`${change.schedule_id}:${change.action}`}
              proposalIndex={index}
            />
          ))}
          {finalQuery && <FinalResultTabs trace={finalQuery} />}
        </CardContent>
      </Card>
      {activities.length > 0 && (
        <EvidenceSection
          activities={activities}
          queries={queries}
          toolsCompleted={activities.length}
        />
      )}
    </motion.div>
  )
}


function ScheduleChangeActions({
  change,
  chatId,
  itemId,
  proposalIndex,
}: {
  change: ScheduleChangeProposal
  chatId: string
  itemId: string
  proposalIndex: number
}) {
  const apply = useApplyChatScheduleChange(chatId, itemId)
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-amber-500/20 bg-amber-500/5 p-4">
      <div>
        <p className="text-sm font-medium">
          {change.action[0].toUpperCase() + change.action.slice(1)} {change.schedule_name}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">{change.reason}</p>
      </div>
      <Button
        disabled={apply.isPending}
        onClick={() => apply.mutate(proposalIndex)}
        size="sm"
        variant={change.action === "delete" ? "destructive" : "outline"}
      >
        Approve
      </Button>
    </div>
  )
}


function GraphRunCard({ runId, label }: { runId: string; label: string }) {
  const run = useGraphRun(runId)
  const value = run.data
  const terminal =
    value && !["queued", "running"].includes(value.status)
  const completed = value
    ? Math.max(0, value.request_count - value.pending_request_count)
    : 0
  const percent = terminal
    ? 100
    : value && value.request_count > 0
      ? Math.round((completed / value.request_count) * 100)
      : 4

  return (
    <div className="ml-11 rounded-xl border bg-card/65 p-4">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-medium">
            {terminal ? (
              <CheckCircle2Icon className="size-4 text-primary" />
            ) : (
              <LoaderCircleIcon className="size-4 animate-spin text-primary" />
            )}
            <span className="truncate">Graph acquisition</span>
            <Badge variant="secondary">{value?.status ?? "loading"}</Badge>
          </div>
          <p className="mt-1 truncate text-xs text-muted-foreground">{label}</p>
        </div>
        <Button
          nativeButton={false}
          render={<a href="/crawls/metrics" />}
          size="sm"
          variant="outline"
        >
          View activity
        </Button>
      </div>
      <Progress className="mt-3" value={percent} />
      {value && (
        <p className="mt-2 font-mono text-[10px] text-muted-foreground">
          {completed} completed · {value.pending_request_count} active · {value.failed_request_count} failed
        </p>
      )}
    </div>
  )
}


function HistoryItem({ item, chatId }: { item: ChatItem; chatId: string }) {
  const content = item.content
  if (content.kind === "user_message") {
    return (
      <div className="flex justify-end pl-12">
        <div className="max-w-2xl whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground shadow-sm">
          {content.text}
        </div>
      </div>
    )
  }
  if (content.kind === "assistant_turn") {
    return (
      <AssistantTurnCard chatId={chatId} content={content} itemId={item.id} />
    )
  }
  if (content.action === "graph_run_started" && content.graph_run_id) {
    return <GraphRunCard label={content.label} runId={content.graph_run_id} />
  }
  return (
    <div className="ml-11 rounded-xl border bg-muted/25 px-4 py-3 text-sm text-muted-foreground">
      {content.label}
    </div>
  )
}


type ChatPageProps = {
  chatId: string | null
  onChatChange: (chatId: string | null) => void
  onBusyChange: (busy: boolean) => void
}


export function ChatPage({
  chatId: activeChatId,
  onChatChange,
  onBusyChange,
}: ChatPageProps) {
  const [question, setQuestion] = useState("")
  const [running, setRunning] = useState(false)
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [summary, setSummary] = useState("")
  const [queries, setQueries] = useState<SearchQueryTrace[]>([])
  const [activities, setActivities] = useState<SearchActivity[]>([])
  const [plans, setPlans] = useState<AcquisitionPlan[]>([])
  const [scheduleChanges, setScheduleChanges] = useState<ScheduleChangeProposal[]>([])
  const [persistedItemId, setPersistedItemId] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const pendingAfterSequence = useRef(0)
  const composer = useRef<HTMLTextAreaElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const chat = useChat(activeChatId)
  const createChat = useCreateChat()

  useEffect(
    () => () => {
      controller.current?.abort()
      onBusyChange(false)
    },
    [onBusyChange]
  )
  useEffect(() => {
    const frame = requestAnimationFrame(() =>
      bottom.current?.scrollIntoView({
        behavior: running ? "auto" : "smooth",
        block: "end",
      })
    )
    return () => cancelAnimationFrame(frame)
  }, [
    activities.length,
    chat.data?.items.length,
    pendingUser,
    persistedItemId,
    plans,
    queries.length,
    running,
    summary,
  ])
  useEffect(() => {
    if (!question && composer.current) composer.current.style.height = "auto"
  }, [question])

  function resetTurn() {
    setPendingUser(null)
    setStatus(null)
    setSummary("")
    setQueries([])
    setActivities([])
    setPlans([])
    setScheduleChanges([])
    setPersistedItemId(null)
  }

  function applyEvent(event: SearchEvent) {
    if (event.type === "agent.status") setStatus(event.message)
    if (event.type === "tool.started" && event.call_id && event.tool) {
      setStatus(`${toolLabel(event.tool)}…`)
      setActivities((current) => [
        ...current,
        {
          callId: event.call_id!,
          tool: event.tool!,
          label: toolLabel(event.tool!),
          detail:
            event.arguments && Object.keys(event.arguments).length > 0
              ? JSON.stringify(event.arguments, null, 2)
              : null,
          sql: null,
          results: [],
          result: null,
          status: "running",
        },
      ])
    }
    if (event.type === "tool.completed" && event.call_id) {
      setActivities((current) =>
        current.map((activity) =>
          activity.callId === event.call_id
            ? {
                ...activity,
                status: "completed",
                result: event.result ?? activity.result,
              }
            : activity
        )
      )
    }
    if (event.type === "search.completed" && event.call_id) {
      setActivities((current) =>
        current.map((activity) =>
          activity.callId === event.call_id
            ? { ...activity, results: event.search_results ?? [] }
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
          row_count: 0,
          truncated: false,
          status: "running",
          error: null,
        },
      ])
    }
    if (event.type === "query.completed" && event.call_id && event.sql) {
      setQueries((current) =>
        current.map((query) =>
          query.callId === event.call_id
            ? {
                ...query,
                queryId: event.query_id,
                sql: event.sql!,
                columns: event.columns ?? [],
                columnTypes: event.column_types ?? [],
                rows: event.rows ?? [],
                row_count: event.row_count ?? (event.rows?.length ?? 0),
                truncated: event.truncated ?? false,
                status: "completed",
              }
            : query
        )
      )
    }
    if (event.type === "query.failed" && event.call_id) {
      setQueries((current) =>
        current.map((query) =>
          query.callId === event.call_id
            ? { ...query, status: "failed", error: event.message }
            : query
        )
      )
    }
    if (event.type === "summary.delta" && event.delta) {
      setStatus("Writing response…")
      setSummary((current) => current + event.delta)
    }
    if (event.type === "run.completed") {
      setSummary(event.summary ?? "")
      setPlans(event.acquisition_plans ?? [])
      setScheduleChanges(event.schedule_changes ?? [])
      setPersistedItemId(event.chat_item_id)
    }
    if (event.type === "run.failed") {
      setSummary(event.message ?? "The Atlas agent could not complete this turn.")
      setPersistedItemId(event.chat_item_id)
      toast.error(event.message ?? "The Atlas agent failed.")
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const message = question.trim()
    if (!message || running) return
    try {
      let chatId = activeChatId
      if (!chatId) {
        const created = await createChat.mutateAsync()
        chatId = created.id
        onChatChange(chatId)
      }
      controller.current = new AbortController()
      setRunning(true)
      onBusyChange(true)
      pendingAfterSequence.current =
        chat.data?.items.at(-1)?.sequence ?? 0
      setPendingUser(message)
      setQuestion("")
      setStatus("Starting Atlas…")
      setSummary("")
      setQueries([])
      setActivities([])
      setPlans([])
      setScheduleChanges([])
      setPersistedItemId(null)
      await streamChatTurn(chatId, message, applyEvent, controller.current.signal)
      await queryClient.invalidateQueries({ queryKey: ["chats"] })
      resetTurn()
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        await queryClient.invalidateQueries({ queryKey: ["chats"] })
        resetTurn()
      } else {
        toast.error(extractApiError(error))
      }
    } finally {
      setRunning(false)
      onBusyChange(false)
      setStatus(null)
    }
  }

  function stop() {
    controller.current?.abort()
    setRunning(false)
    onBusyChange(false)
    setStatus(null)
  }

  const persistedItems = chat.data?.items ?? []
  const pendingUserIsPersisted =
    pendingUser !== null &&
    persistedItems.some(
      (item) =>
        item.sequence > pendingAfterSequence.current &&
        item.content.kind === "user_message" &&
        item.content.text === pendingUser
    )
  const visiblePersistedItems = persistedItemId
    ? persistedItems.filter((item) => item.id !== persistedItemId)
    : persistedItems
  const hasConversation = persistedItems.length > 0 || pendingUser !== null
  const transientContent = useMemo<AssistantTurnContent>(
    () => ({
      kind: "assistant_turn",
      summary,
      tools: activities.map((activity) => ({
        call_id: activity.callId,
        tool: activity.tool,
        arguments: {},
        result_preview: activity.result,
        search_results: activity.results,
        status: "completed",
        error: null,
      })),
      queries: queries.map((query) => ({
        call_id: query.callId,
        query_id: query.queryId,
        sql: query.sql,
        columns: query.columns,
        column_types: query.columnTypes,
        rows: query.rows,
        row_count: query.rows.length,
        truncated: false,
        status: query.status === "running" ? "completed" : query.status,
        error: query.error,
      })),
      acquisition_plans: plans,
      schedule_changes: scheduleChanges,
      failed: false,
    }),
    [activities, plans, queries, scheduleChanges, summary]
  )

  return (
    <main className="mx-auto flex min-h-full w-full max-w-6xl flex-col pb-4">
        <section className="flex min-h-full min-w-0 flex-1 flex-col">
          {!hasConversation && (
            <div className="flex min-h-[52svh] flex-col items-center justify-center text-center">
              <div className="flex items-center gap-2 text-xs font-medium tracking-[0.18em] text-primary uppercase">
                <BotIcon className="size-4" /> Ask Atlas
              </div>
              <h1 className="mt-3 text-4xl leading-tight font-semibold tracking-[-0.035em] lg:text-5xl">
                What do you want to know?
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-6 text-muted-foreground">
                Analyze retained evidence, plan new acquisition, or work through
                the question with Atlas over several turns.
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
            </div>
          )}

          {hasConversation && (
            <div className="mb-6 flex items-center gap-3 border-b pb-4">
              <span className="flex size-9 items-center justify-center rounded-xl bg-primary/10">
                <BotIcon className="size-4 text-primary" />
              </span>
              <div className="min-w-0">
                <h1 className="truncate text-lg font-semibold">
                  {chat.data?.title ?? "Atlas conversation"}
                </h1>
                <p className="text-xs text-muted-foreground">
                  Analysis and acquisition, grounded in Atlas tools
                </p>
              </div>
            </div>
          )}

          <div className="space-y-7 pb-4" aria-live="polite">
            {visiblePersistedItems.map((item) => (
              <HistoryItem chatId={activeChatId!} item={item} key={item.id} />
            ))}
            {pendingUser && !pendingUserIsPersisted && (
              <div className="flex justify-end pl-12">
                <div className="max-w-2xl whitespace-pre-wrap rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground shadow-sm">
                  {pendingUser}
                </div>
              </div>
            )}
            <AnimatePresence mode="popLayout">
              {running && !summary && (
                <LiveActivity activities={activities} status={status} />
              )}
            </AnimatePresence>
            {summary && activeChatId && (
              <AssistantTurnCard
                chatId={activeChatId}
                content={transientContent}
                itemId={persistedItemId ?? "pending"}
              />
            )}
            <div className="scroll-mb-32" ref={bottom} />
          </div>

          <form
            className={`${hasConversation ? "sticky bottom-0 mt-auto pt-8 pb-2" : "mt-0"} z-20 bg-linear-to-t from-background via-background/96 to-transparent`}
            onSubmit={submit}
          >
            <div className="relative isolate overflow-hidden rounded-[1.75rem] border border-transparent bg-card shadow-[0_4px_24px_rgb(0_0_0/0.18)] ring-1 ring-foreground/10 transition-[border-color,box-shadow] focus-within:border-primary/35 focus-within:shadow-[0_5px_28px_rgb(0_0_0/0.22),0_0_28px_rgba(56,189,248,0.14)] focus-within:ring-4 focus-within:ring-primary/10">
              <SearchIcon className="pointer-events-none absolute top-[1.1rem] left-4 size-4 text-muted-foreground" />
              <Textarea
                aria-label="Message Atlas"
                autoFocus
                className="max-h-40 min-h-13 resize-none overflow-y-auto rounded-[inherit] border-0! bg-transparent py-3.5 pr-32 pl-11 text-sm leading-6 shadow-none focus-visible:border-transparent! focus-visible:ring-0!"
                disabled={running}
                onChange={(event) => {
                  setQuestion(event.target.value)
                  event.currentTarget.style.height = "auto"
                  event.currentTarget.style.height = `${Math.min(event.currentTarget.scrollHeight, 160)}px`
                }}
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
                placeholder={
                  hasConversation
                    ? "Continue the conversation…"
                    : "Ask about retained data or plan what Atlas should acquire…"
                }
                ref={composer}
                rows={1}
                value={question}
              />
              <div className="absolute right-2 bottom-2">
                {running ? (
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
                    className="h-9 rounded-full px-4 shadow-[0_0_20px_rgba(56,189,248,0.28)] hover:shadow-[0_0_28px_rgba(56,189,248,0.48)]"
                    disabled={!question.trim() || createChat.isPending}
                    size="sm"
                    type="submit"
                  >
                    Ask Atlas
                  </Button>
                )}
              </div>
            </div>
            <p className="mt-2 px-3 text-[10px] text-muted-foreground">
              Enter to send · Shift+Enter for a new line.
            </p>
          </form>
        </section>
    </main>
  )
}
