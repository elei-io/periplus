import { useEffect, useMemo, useState } from "react"
import {
  ArchiveIcon,
  CalendarClockIcon,
  CalendarIcon,
  ChevronDownIcon,
  CircleHelpIcon,
  CopyIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SaveIcon,
  XCircleIcon,
} from "lucide-react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { extractApiError } from "@/lib/api"
import {
  archiveTask,
  copyTask,
  createTask,
  listTasks,
  unarchiveTask,
  updateTask,
} from "@/lib/tasks-api"
import { cn } from "@/lib/utils"
import { searchProviders } from "@/types/search"
import type { SearchProvider } from "@/types/search"
import type {
  TaskCreate,
  TaskFilters,
  TaskPrimitive,
  TaskRecord,
  TaskSchedule,
} from "@/types/tasks"

const primitives: TaskPrimitive[] = [
  "search",
  "index",
  "crawl",
  "schema",
  "extract",
  "calibrate",
]

function taskFiltersFromLocation(): TaskFilters {
  const primitive = new URLSearchParams(window.location.search).get(
    "primitive"
  )

  return primitive && primitives.includes(primitive as TaskPrimitive)
    ? { primitive: primitive as TaskPrimitive }
    : {}
}

const scheduleKinds = ["once", "cron", "interval"] as const
type ScheduleKind = (typeof scheduleKinds)[number]

type ScheduleFields = {
  runAt: string
  cronExpr: string
  everySeconds: string
  timezone: string
  startAt: string
  endAt: string
}

type TaskInputFields = {
  query: string
  searchProvider: SearchProvider
  maxPages: string
  reuseExisting: boolean
  forceCalibration: boolean
  url: string
  urls: string[]
  prompt: string
  extractData: boolean
  extractQueryParams: boolean
  maxDepth: string
  dedupe: boolean
  includeCrawl: string
  excludeCrawl: string
  includeResult: string
  excludeResult: string
}

const defaultInputFields: TaskInputFields = {
  query: "site:example.com atlas",
  searchProvider: "duckduckgo",
  maxPages: "1",
  reuseExisting: true,
  forceCalibration: false,
  url: "https://example.com",
  urls: ["https://example.com"],
  prompt: "Extract the main heading.",
  extractData: true,
  extractQueryParams: true,
  maxDepth: "0",
  dedupe: false,
  includeCrawl: "",
  excludeCrawl: "",
  includeResult: "",
  excludeResult: "",
}

function lines(value: string) {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
}

function splitMultiValue(value: string) {
  return value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function numberOrDefault(value: string, fallback: number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}

function formatDateTime(value: string | null) {
  if (!value) {
    return "Never"
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value))
}

function fromLocalDateTimeInput(value: string) {
  if (!value) {
    return null
  }

  return new Date(value).toISOString()
}

function toLocalDateTimeInput(value: string | null | undefined) {
  if (!value) {
    return ""
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ""
  }

  const timezoneOffsetMs = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - timezoneOffsetMs).toISOString().slice(0, 16)
}

function datePart(value: string) {
  return value.includes("T") ? value.split("T")[0] : ""
}

function timePart(value: string) {
  return value.includes("T") ? value.split("T")[1]?.slice(0, 5) ?? "" : ""
}

function dateFromDatePart(value: string) {
  if (!value) {
    return undefined
  }

  const [year, month, day] = value.split("-").map(Number)
  if (!year || !month || !day) {
    return undefined
  }

  return new Date(year, month - 1, day)
}

function localDatePart(date: Date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

function combineLocalDateTime(date: string, time: string) {
  if (!date) {
    return ""
  }

  return `${date}T${time || "00:00"}`
}

function formatDateButtonLabel(value: string) {
  const selectedDate = dateFromDatePart(datePart(value))
  if (!selectedDate) {
    return "Pick date"
  }

  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    year: "numeric",
  }).format(selectedDate)
}

function scheduleKind(schedule: TaskSchedule | null): ScheduleKind {
  return schedule?.kind ?? "once"
}

function scheduleFieldsFromSchedule(schedule: TaskSchedule | null): ScheduleFields {
  if (schedule?.kind === "once") {
    return {
      runAt: toLocalDateTimeInput(schedule.run_at),
      cronExpr: "0 7 * * *",
      everySeconds: "3600",
      timezone: schedule.timezone,
      startAt: "",
      endAt: "",
    }
  }

  if (schedule?.kind === "cron") {
    return {
      runAt: "",
      cronExpr: schedule.expr,
      everySeconds: "3600",
      timezone: schedule.timezone,
      startAt: toLocalDateTimeInput(schedule.start_at),
      endAt: toLocalDateTimeInput(schedule.end_at),
    }
  }

  if (schedule?.kind === "interval") {
    return {
      runAt: "",
      cronExpr: "0 7 * * *",
      everySeconds: String(schedule.every_seconds),
      timezone: schedule.timezone,
      startAt: toLocalDateTimeInput(schedule.start_at),
      endAt: toLocalDateTimeInput(schedule.end_at),
    }
  }

  return {
    runAt: "",
    cronExpr: "0 7 * * *",
    everySeconds: "3600",
    timezone: "UTC",
    startAt: "",
    endAt: "",
  }
}

function scheduleSummary(schedule: TaskSchedule | null) {
  if (!schedule) {
    return "Manual"
  }

  if (schedule.kind === "once") {
    return `Once at ${formatDateTime(schedule.run_at)}`
  }

  if (schedule.kind === "cron") {
    return `Cron ${schedule.expr}`
  }

  return `Every ${schedule.every_seconds}s`
}

function buildSchedule(
  kind: ScheduleKind,
  fields: ScheduleFields
): TaskSchedule {
  if (kind === "once") {
    return {
      kind: "once",
      run_at: fromLocalDateTimeInput(fields.runAt) ?? new Date().toISOString(),
      timezone: fields.timezone || "UTC",
    }
  }

  if (kind === "cron") {
    return {
      kind: "cron",
      expr: fields.cronExpr || "0 7 * * *",
      timezone: fields.timezone || "UTC",
      start_at: fromLocalDateTimeInput(fields.startAt),
      end_at: fromLocalDateTimeInput(fields.endAt),
    }
  }

  return {
    kind: "interval",
    every_seconds: Math.max(1, Number(fields.everySeconds) || 3600),
    timezone: fields.timezone || "UTC",
    start_at: fromLocalDateTimeInput(fields.startAt),
    end_at: fromLocalDateTimeInput(fields.endAt),
  }
}

function buildTaskInput(primitive: TaskPrimitive, fields: TaskInputFields) {
  if (primitive === "search") {
    if (!fields.query.trim()) {
      throw new Error("Search query is required.")
    }

    return {
      query: fields.query.trim(),
      provider: fields.searchProvider,
      max_pages: Math.max(1, numberOrDefault(fields.maxPages, 1)),
    }
  }

  if (primitive === "crawl") {
    const urls = fields.urls.map((url) => url.trim()).filter(Boolean)
    if (urls.length === 0) {
      throw new Error("Add at least one URL to crawl.")
    }

    return {
      urls,
    }
  }

  if (primitive === "index") {
    if (!fields.url.trim()) {
      throw new Error("Index URL is required.")
    }

    return {
      url: fields.url.trim(),
      max_depth: Math.max(0, numberOrDefault(fields.maxDepth, 0)),
      dedupe: fields.dedupe,
      include_crawl: lines(fields.includeCrawl),
      exclude_crawl: lines(fields.excludeCrawl),
      include_result: lines(fields.includeResult),
      exclude_result: lines(fields.excludeResult),
    }
  }

  if (primitive === "calibrate") {
    if (!fields.url.trim()) {
      throw new Error("Calibration URL is required.")
    }

    return {
      url: fields.url.trim(),
      force: fields.forceCalibration,
    }
  }

  if (!fields.url.trim()) {
    throw new Error("Page URL is required.")
  }

  if (primitive === "extract" && !fields.extractData && !fields.extractQueryParams) {
    throw new Error("Enable at least one extraction mode.")
  }

  if ((primitive === "schema" || fields.extractData) && !fields.prompt.trim()) {
    throw new Error("Extraction prompt is required.")
  }

  const input = {
    url: fields.url.trim(),
    prompt: fields.prompt.trim() || null,
    schema_type: "css",
  }

  if (primitive === "extract") {
    return {
      ...input,
      extract_data: fields.extractData,
      extract_query_params: fields.extractQueryParams,
    }
  }

  return input
}

export function TasksPage() {
  const queryClient = useQueryClient()
  const [filters, setFilters] = useState<TaskFilters>(taskFiltersFromLocation)
  const [showArchived, setShowArchived] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [scheduleTask, setScheduleTask] = useState<TaskRecord | null>(null)
  const taskFilters = useMemo(
    () => ({
      ...filters,
      archived: showArchived ? undefined : false,
    }),
    [filters, showArchived]
  )

  useEffect(() => {
    const url = new URL(window.location.href)
    if (filters.primitive) {
      url.searchParams.set("primitive", filters.primitive)
    } else {
      url.searchParams.delete("primitive")
    }
    window.history.replaceState(null, "", url)
  }, [filters.primitive])

  const tasksQuery = useQuery({
    queryKey: ["tasks", taskFilters],
    queryFn: () => listTasks(taskFilters),
  })

  const invalidateTasks = async () => {
    await queryClient.invalidateQueries({ queryKey: ["tasks"] })
  }

  const createMutation = useMutation({
    mutationFn: createTask,
    onSuccess: async () => {
      toast.success("Task created.")
      setCreateOpen(false)
      await invalidateTasks()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const copyMutation = useMutation({
    mutationFn: copyTask,
    onSuccess: async () => {
      toast.success("Task copied as archived draft.")
      await invalidateTasks()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const archiveMutation = useMutation({
    mutationFn: archiveTask,
    onSuccess: async () => {
      toast.success("Task archived.")
      await invalidateTasks()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const unarchiveMutation = useMutation({
    mutationFn: unarchiveTask,
    onSuccess: async () => {
      toast.success("Task unarchived.")
      await invalidateTasks()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const scheduleMutation = useMutation({
    mutationFn: ({
      taskId,
      schedule,
    }: {
      taskId: string
      schedule: TaskSchedule | null
    }) => updateTask(taskId, { schedule }),
    onSuccess: async () => {
      toast.success("Task schedule saved.")
      setScheduleTask(null)
      await invalidateTasks()
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  return (
    <div className="flex min-h-[calc(100svh-7rem)] w-full flex-col overflow-hidden rounded-lg border bg-background/80">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate text-sm font-medium">Tasks</h1>
          <Badge variant="outline">{tasksQuery.data?.length ?? 0}</Badge>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TaskSelect
            aria-label="Primitive filter"
            value={filters.primitive ?? "all"}
            options={[
              { value: "all", label: "All primitives" },
              ...primitives.map((primitive) => ({
                value: primitive,
                label: primitive,
              })),
            ]}
            onChange={(value) =>
              setFilters((current) => ({
                ...current,
                primitive:
                  value === "all" ? undefined : (value as TaskPrimitive),
              }))
            }
          />
          <TaskSelect
            aria-label="Origin filter"
            value={filters.origin ?? "all"}
            options={[
              { value: "all", label: "All origins" },
              { value: "human", label: "Human seeded" },
              { value: "effect", label: "Effect created" },
            ]}
            onChange={(value) =>
              setFilters((current) => ({
                ...current,
                origin:
                  value === "all"
                    ? undefined
                    : (value as NonNullable<TaskFilters["origin"]>),
              }))
            }
          />
          <ToolbarIconButton
            label="Refresh tasks"
            onClick={() => void tasksQuery.refetch()}
            disabled={tasksQuery.isFetching}
          >
            <RefreshCwIcon
              className={cn(tasksQuery.isFetching && "animate-spin")}
            />
          </ToolbarIconButton>
          <ToolbarIconButton
            label={showArchived ? "Hide archived" : "Show archived"}
            variant={showArchived ? "secondary" : "outline"}
            onClick={() => setShowArchived((current) => !current)}
          >
            <ArchiveIcon />
          </ToolbarIconButton>
          <Button type="button" size="sm" onClick={() => setCreateOpen(true)}>
            <PlusIcon />
            New
          </Button>
        </div>
      </div>

      <Table containerClassName="min-h-0 flex-1">
        <TableHeader>
          <TableRow>
            <TableHead className="w-[26%]">Name</TableHead>
            <TableHead>Primitive</TableHead>
            <TableHead>Schedule</TableHead>
            <TableHead>Next</TableHead>
            <TableHead>Last</TableHead>
            <TableHead>Origin</TableHead>
            <TableHead className="w-28 text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tasksQuery.data?.map((task) => (
            <TaskRow
              key={task.id}
              task={task}
              isArchiving={archiveMutation.isPending}
              isCopying={copyMutation.isPending}
              isScheduling={scheduleMutation.isPending}
              isUnarchiving={unarchiveMutation.isPending}
              onSchedule={() => setScheduleTask(task)}
              onCopy={() => copyMutation.mutate(task.id)}
              onArchive={() => {
                if (window.confirm(`Archive ${task.name}?`)) {
                  archiveMutation.mutate(task.id)
                }
              }}
              onUnarchive={() => unarchiveMutation.mutate(task.id)}
            />
          ))}
          {tasksQuery.data?.length === 0 && (
            <TableRow>
              <TableCell
                colSpan={7}
                className="h-24 text-center text-muted-foreground"
              >
                No tasks match the current filters.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      <TaskCreateDialog
        open={createOpen}
        isSaving={createMutation.isPending}
        onOpenChange={setCreateOpen}
        onCreate={(input) => createMutation.mutate(input)}
      />
      {scheduleTask && (
        <TaskScheduleDialog
          key={`${scheduleTask.id}-${scheduleTask.updated_at}`}
          task={scheduleTask}
          isSaving={scheduleMutation.isPending}
          onOpenChange={(open) => {
            if (!open) {
              setScheduleTask(null)
            }
          }}
          onSave={(schedule) =>
            scheduleMutation.mutate({
              taskId: scheduleTask.id,
              schedule,
            })
          }
        />
      )}
    </div>
  )
}

function TaskRow({
  task,
  isArchiving,
  isCopying,
  isScheduling,
  isUnarchiving,
  onSchedule,
  onCopy,
  onArchive,
  onUnarchive,
}: {
  task: TaskRecord
  isArchiving: boolean
  isCopying: boolean
  isScheduling: boolean
  isUnarchiving: boolean
  onSchedule: () => void
  onCopy: () => void
  onArchive: () => void
  onUnarchive: () => void
}) {
  const isArchived = Boolean(task.archived_at)

  return (
    <TableRow className={cn(isArchived && "bg-muted/20 opacity-60")}>
      <TableCell className="max-w-0">
        <div className="truncate font-medium">{task.name}</div>
        <div className="truncate text-[0.6875rem] text-muted-foreground">
          {task.identity_key || task.id}
        </div>
      </TableCell>
      <TableCell>
        <Badge variant="secondary">{task.primitive}</Badge>
      </TableCell>
      <TableCell className="max-w-[16rem] truncate">
        {scheduleSummary(task.schedule_json)}
      </TableCell>
      <TableCell>{formatDateTime(task.next_run_at)}</TableCell>
      <TableCell>{formatDateTime(task.last_run_at)}</TableCell>
      <TableCell>
        <Badge variant="outline">
          {task.created_by_effect_run_id ? "Effect" : "Human"}
        </Badge>
      </TableCell>
      <TableCell>
        <div className="flex justify-end gap-1">
          {isArchived ? (
            <RowIconButton
              label="Unarchive task"
              variant="ghost"
              disabled={isUnarchiving}
              onClick={onUnarchive}
            >
              <RotateCcwIcon />
            </RowIconButton>
          ) : (
            <>
              <RowIconButton
                label="Schedule task"
                variant="ghost"
                disabled={isScheduling}
                onClick={onSchedule}
              >
                <CalendarClockIcon />
              </RowIconButton>
              <RowIconButton
                label="Copy task"
                variant="ghost"
                disabled={isCopying}
                onClick={onCopy}
              >
                <CopyIcon />
              </RowIconButton>
              <RowIconButton
                label="Archive task"
                variant="destructive"
                disabled={isArchiving}
                onClick={onArchive}
              >
                <ArchiveIcon />
              </RowIconButton>
            </>
          )}
        </div>
      </TableCell>
    </TableRow>
  )
}

function ToolbarIconButton({
  label,
  children,
  variant = "outline",
  disabled,
  onClick,
}: {
  label: string
  children: React.ReactNode
  variant?: React.ComponentProps<typeof Button>["variant"]
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant={variant}
            size="icon"
            disabled={disabled}
            onClick={onClick}
          />
        }
      >
        {children}
        <span className="sr-only">{label}</span>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function RowIconButton({
  label,
  children,
  variant,
  disabled,
  onClick,
}: {
  label: string
  children: React.ReactNode
  variant: React.ComponentProps<typeof Button>["variant"]
  disabled?: boolean
  onClick: () => void
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant={variant}
            size="icon-sm"
            disabled={disabled}
            onClick={onClick}
          />
        }
      >
        {children}
        <span className="sr-only">{label}</span>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function TaskCreateDialog({
  open,
  isSaving,
  onOpenChange,
  onCreate,
}: {
  open: boolean
  isSaving: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (input: TaskCreate) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100svh-2rem)] overflow-hidden sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>Create task</DialogTitle>
          <DialogDescription>
            Choose the task primitive, then configure its target.
          </DialogDescription>
        </DialogHeader>
        <TaskCreateForm isSaving={isSaving} onCreate={onCreate} />
      </DialogContent>
    </Dialog>
  )
}

function TaskCreateForm({
  isSaving,
  onCreate,
}: {
  isSaving: boolean
  onCreate: (input: TaskCreate) => void
}) {
  const [name, setName] = useState("")
  const [primitive, setPrimitive] = useState<TaskPrimitive>("crawl")
  const [step, setStep] = useState<1 | 2>(1)
  const [knobsOpen, setKnobsOpen] = useState(false)
  const [inputFields, setInputFields] =
    useState<TaskInputFields>(defaultInputFields)
  const [formError, setFormError] = useState<string | null>(null)

  const submit = () => {
    setFormError(null)
    if (!name.trim()) {
      setFormError("Task name is required.")
      return
    }

    if (step === 1) {
      setStep(2)
      return
    }

    let input: Record<string, unknown>
    try {
      input = buildTaskInput(primitive, inputFields)
    } catch (error) {
      setFormError(
        error instanceof Error ? error.message : "Task input is invalid."
      )
      return
    }

    onCreate({
      name: name.trim(),
      primitive,
      input,
      schedule: null,
    })
  }

  return (
    <div className="grid min-h-0 gap-4 overflow-y-auto pr-1">
      {formError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-xs text-destructive">
          {formError}
        </div>
      )}

      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Badge variant={step === 1 ? "default" : "secondary"}>Step 1</Badge>
        <span>Primitive and name</span>
        <span>/</span>
        <Badge variant={step === 2 ? "default" : "secondary"}>Step 2</Badge>
        <span>Target and options</span>
      </div>

      {step === 1 ? (
        <div className="grid gap-3 md:grid-cols-[1fr_12rem]">
          <Field
            label="Name"
            hint="Human-readable label shown in the task list and run history."
          >
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field
            label="Primitive"
            hint="The action this task will run when scheduled or triggered."
          >
            <TaskSelect
              value={primitive}
              className="w-full"
              options={primitives.map((item) => ({
                value: item,
                label: item,
              }))}
              onChange={(value) => {
                const nextPrimitive = value as TaskPrimitive
                setPrimitive(nextPrimitive)
              }}
            />
          </Field>
        </div>
      ) : (
        <PrimitiveInputBuilder
          primitive={primitive}
          fields={inputFields}
          knobsOpen={knobsOpen}
          onChange={setInputFields}
          onKnobsOpenChange={setKnobsOpen}
        />
      )}

      <DialogFooter className="gap-2 border-t pt-3 sm:justify-between">
        {step === 2 && (
          <Button
            type="button"
            variant="outline"
            disabled={isSaving}
            onClick={() => {
              setFormError(null)
              setStep(1)
            }}
          >
            Back
          </Button>
        )}
        <Button type="button" onClick={submit} disabled={isSaving}>
          {step === 1 ? (
            <>
              Next
              <ChevronDownIcon className="-rotate-90" />
            </>
          ) : (
            <>
              <SaveIcon />
              Create
            </>
          )}
        </Button>
      </DialogFooter>
    </div>
  )
}

function PrimitiveInputBuilder({
  primitive,
  fields,
  knobsOpen,
  onChange,
  onKnobsOpenChange,
}: {
  primitive: TaskPrimitive
  fields: TaskInputFields
  knobsOpen: boolean
  onChange: React.Dispatch<React.SetStateAction<TaskInputFields>>
  onKnobsOpenChange: (open: boolean) => void
}) {
  const patch = (next: Partial<TaskInputFields>) => {
    onChange((current) => ({ ...current, ...next }))
  }

  return (
    <div className="grid content-start gap-3">
      <div className="grid gap-3 rounded-lg border bg-muted/20 p-3">
        <FieldLabel hint="Required target fields Atlas serializes into the task input payload.">
          Target
        </FieldLabel>
        <PrimitiveTargetFields
          primitive={primitive}
          fields={fields}
          onChange={patch}
        />
      </div>

      <Collapsible open={knobsOpen} onOpenChange={onKnobsOpenChange}>
        <div className="grid gap-3 rounded-lg border bg-muted/20 p-3">
          <CollapsibleTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                className="h-auto justify-between px-0 py-0 hover:bg-transparent"
              />
            }
          >
            <FieldLabel hint="Optional primitive-specific tuning. Defaults are chosen for the common case.">
              Advanced options
            </FieldLabel>
            <ChevronDownIcon
              className={cn(
                "transition-transform",
                knobsOpen && "rotate-180"
              )}
            />
          </CollapsibleTrigger>
          <CollapsibleContent className="grid gap-3">
            <PrimitiveKnobFields
              primitive={primitive}
              fields={fields}
              onChange={patch}
            />
          </CollapsibleContent>
        </div>
      </Collapsible>
    </div>
  )
}

function PrimitiveTargetFields({
  primitive,
  fields,
  onChange,
}: {
  primitive: TaskPrimitive
  fields: TaskInputFields
  onChange: (next: Partial<TaskInputFields>) => void
}) {
  const patch = onChange

  return (
    <div className="grid gap-3">
      {primitive === "search" && (
        <div className="grid gap-3">
          <Field label="Query" hint="Search phrase sent to the search provider.">
            <Input
              value={fields.query}
              onChange={(event) => patch({ query: event.target.value })}
            />
          </Field>
          <Field label="Provider" hint="Web search provider Atlas should crawl for this task.">
            <TaskSelect
              value={fields.searchProvider}
              options={searchProviders}
              onChange={(value) => patch({ searchProvider: value as SearchProvider })}
              aria-label="Search provider"
            />
          </Field>
        </div>
      )}

      {primitive === "crawl" && (
        <Field
          label="URLs"
          hint="One URL per row. Pasted comma-separated or newline-separated URLs become separate rows."
        >
          <UrlRowsInput
            value={fields.urls}
            onChange={(urls) => patch({ urls })}
          />
        </Field>
      )}

      {primitive === "index" && (
        <Field label="URL" hint="Starting page Atlas crawls to discover links.">
          <Input
            value={fields.url}
            onChange={(event) => patch({ url: event.target.value })}
          />
        </Field>
      )}

      {primitive === "calibrate" && (
        <div className="grid gap-3">
          <Field label="URL" hint="Representative page used to test crawl transport templates.">
            <Input
              value={fields.url}
              onChange={(event) => patch({ url: event.target.value })}
            />
          </Field>
          <Field label="Force" hint="Re-run calibration even when an enabled policy already exists.">
            <div className="flex h-7 items-center gap-2">
              <Switch
                checked={fields.forceCalibration}
                onCheckedChange={(forceCalibration) => patch({ forceCalibration })}
              />
              <span className="text-xs text-muted-foreground">
                {fields.forceCalibration ? "On" : "Off"}
              </span>
            </div>
          </Field>
        </div>
      )}

      {(primitive === "schema" || primitive === "extract") && (
        <div className="grid gap-3">
          <Field
            label="URL"
            hint={
              primitive === "schema"
                ? "Sample page used to generate and validate the extraction schema."
                : "Page Atlas crawls before applying the extraction prompt."
            }
          >
            <Input
              value={fields.url}
              onChange={(event) => patch({ url: event.target.value })}
            />
          </Field>
          <Field
            label="Prompt"
            hint={
              primitive === "extract" && !fields.extractData
                ? "Only needed when data extraction is enabled."
                : "Natural-language instructions describing the structured data Atlas should extract."
            }
          >
            <Textarea
              value={fields.prompt}
              className="min-h-24 resize-y"
              onChange={(event) => patch({ prompt: event.target.value })}
            />
          </Field>
        </div>
      )}
    </div>
  )
}

function PrimitiveKnobFields({
  primitive,
  fields,
  onChange,
}: {
  primitive: TaskPrimitive
  fields: TaskInputFields
  onChange: (next: Partial<TaskInputFields>) => void
}) {
  return (
    <>
      {primitive === "search" && (
        <Field
          label="Max pages"
          hint="Maximum number of pages Atlas should crawl."
        >
          <Input
            type="number"
            min={1}
            max={25}
            value={fields.maxPages}
            onChange={(event) => onChange({ maxPages: event.target.value })}
          />
        </Field>
      )}

      {primitive === "index" && (
        <>
          <div className="grid gap-3 md:grid-cols-2">
            <Field
              label="Max depth"
              hint="How many link hops Atlas may follow from the starting page."
            >
              <Input
                type="number"
                min={0}
                value={fields.maxDepth}
                onChange={(event) => onChange({ maxDepth: event.target.value })}
              />
            </Field>
            <Field
              label="Dedupe links"
              hint="When enabled, duplicate discovered URLs are collapsed in the index result."
            >
              <div className="flex h-7 items-center gap-2">
                <Switch
                  checked={fields.dedupe}
                  onCheckedChange={(dedupe) => onChange({ dedupe })}
                />
                <span className="text-xs text-muted-foreground">
                  {fields.dedupe ? "On" : "Off"}
                </span>
              </div>
            </Field>
          </div>
          <PatternFields fields={fields} onChange={onChange} />
        </>
      )}

      {(primitive === "schema" || primitive === "extract") && (
        <>
          {primitive === "extract" && (
            <div className="grid gap-3 md:grid-cols-2">
              <Field
                label="Data"
                hint="Generate or reuse a DataSchema, then extract structured records."
              >
                <div className="flex h-7 items-center gap-2">
                  <Switch
                    checked={fields.extractData}
                    onCheckedChange={(extractData) => onChange({ extractData })}
                  />
                  <span className="text-xs text-muted-foreground">
                    {fields.extractData ? "On" : "Off"}
                  </span>
                </div>
              </Field>
              <Field
                label="Query params"
                hint="Generate or reuse a QuerySchema from same-page navigation evidence."
              >
                <div className="flex h-7 items-center gap-2">
                  <Switch
                    checked={fields.extractQueryParams}
                    onCheckedChange={(extractQueryParams) => onChange({ extractQueryParams })}
                  />
                  <span className="text-xs text-muted-foreground">
                    {fields.extractQueryParams ? "On" : "Off"}
                  </span>
                </div>
              </Field>
            </div>
          )}
        </>
      )}
    </>
  )
}

function UrlRowsInput({
  value,
  onChange,
}: {
  value: string[]
  onChange: (value: string[]) => void
}) {
  const rows = value.length > 0 ? value : [""]

  const updateRows = (nextRows: string[]) => {
    onChange(nextRows.length > 0 ? nextRows : [""])
  }

  const replaceRow = (index: number, nextValue: string) => {
    const nextRows = [...rows]
    nextRows[index] = nextValue
    updateRows(nextRows)
  }

  const addRows = (index: number, entries: string[]) => {
    const before = rows.slice(0, index)
    const after = rows.slice(index + 1)
    updateRows([...before, ...entries, ...after])
  }

  const removeRow = (index: number) => {
    updateRows(rows.filter((_, rowIndex) => rowIndex !== index))
  }

  return (
    <div className="grid gap-2">
      {rows.map((url, index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
          <Input
            value={url}
            placeholder="https://example.com"
            onChange={(event) => replaceRow(index, event.target.value)}
            onPaste={(event) => {
              const pasted = event.clipboardData.getData("text")
              const entries = splitMultiValue(pasted)
              if (entries.length <= 1) {
                return
              }

              event.preventDefault()
              addRows(index, entries)
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={rows.length === 1}
            onClick={() => removeRow(index)}
          >
            <XCircleIcon />
            <span className="sr-only">Remove URL</span>
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="justify-self-start"
        onClick={() => updateRows([...rows, ""])}
      >
        <PlusIcon />
        Add URL
      </Button>
    </div>
  )
}

function TaskScheduleDialog({
  task,
  isSaving,
  onOpenChange,
  onSave,
}: {
  task: TaskRecord
  isSaving: boolean
  onOpenChange: (open: boolean) => void
  onSave: (schedule: TaskSchedule | null) => void
}) {
  const [kind, setKind] = useState<ScheduleKind>(
    scheduleKind(task.schedule_json as TaskSchedule | null)
  )
  const [fields, setFields] = useState<ScheduleFields>(
    scheduleFieldsFromSchedule(task.schedule_json as TaskSchedule | null)
  )

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Schedule task</DialogTitle>
          <DialogDescription>{task.name}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <Field
            label="Kind"
            hint="Schedule type Atlas uses to decide when to queue this task."
          >
            <TaskSelect
              value={kind}
              className="w-full"
              options={scheduleKinds.map((item) => ({
                value: item,
                label: item,
              }))}
              onChange={(value) => setKind(value as ScheduleKind)}
            />
          </Field>

          {kind === "once" && (
            <Field
              label="Run at"
              hint="Local date and time for a one-off task run."
            >
              <DateTimeField
                value={fields.runAt}
                onChange={(runAt) =>
                  setFields((current) => ({
                    ...current,
                    runAt,
                  }))
                }
              />
            </Field>
          )}

          {kind === "cron" && (
            <Field
              label="Expression"
              hint="Cron expression evaluated in the configured timezone."
            >
              <Input
                value={fields.cronExpr}
                onChange={(event) =>
                  setFields((current) => ({
                    ...current,
                    cronExpr: event.target.value,
                  }))
                }
              />
            </Field>
          )}

          {kind === "interval" && (
            <Field
              label="Every seconds"
              hint="Minimum spacing between scheduled task runs."
            >
              <Input
                type="number"
                min={1}
                value={fields.everySeconds}
                onChange={(event) =>
                  setFields((current) => ({
                    ...current,
                    everySeconds: event.target.value,
                  }))
                }
              />
            </Field>
          )}

          <Field
            label="Timezone"
            hint="Timezone used to interpret cron schedules and display schedule intent."
          >
            <Input
              value={fields.timezone}
              onChange={(event) =>
                setFields((current) => ({
                  ...current,
                  timezone: event.target.value,
                }))
              }
            />
          </Field>

          {(kind === "cron" || kind === "interval") && (
            <div className="grid gap-3 md:grid-cols-2">
              <Field
                label="Start at"
                hint="Optional earliest local date and time when scheduler may queue this task."
              >
                <DateTimeField
                  value={fields.startAt}
                  onChange={(startAt) =>
                    setFields((current) => ({
                      ...current,
                      startAt,
                    }))
                  }
                />
              </Field>
              <Field
                label="End at"
                hint="Optional latest local date and time when scheduler may queue this task."
              >
                <DateTimeField
                  value={fields.endAt}
                  onChange={(endAt) =>
                    setFields((current) => ({
                      ...current,
                      endAt,
                    }))
                  }
                />
              </Field>
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 border-t pt-3 sm:justify-between">
          <Button
            type="button"
            variant="destructive"
            disabled={isSaving || !task.schedule_json}
            onClick={() => onSave(null)}
          >
            <XCircleIcon />
            Remove schedule
          </Button>
          <Button
            type="button"
            disabled={isSaving}
            onClick={() => onSave(buildSchedule(kind, fields))}
          >
            <SaveIcon />
            Save schedule
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DateTimeField({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const selectedDatePart = datePart(value)
  const selectedDate = dateFromDatePart(selectedDatePart)

  return (
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_7rem]">
      <Popover>
        <PopoverTrigger
          render={
            <Button
              type="button"
              variant="outline"
              className={cn(
                "justify-start gap-2 text-left font-normal",
                !selectedDate && "text-muted-foreground"
              )}
            />
          }
        >
          <CalendarIcon />
          <span>{formatDateButtonLabel(value)}</span>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-auto p-0">
          <Calendar
            mode="single"
            selected={selectedDate}
            onSelect={(date) =>
              onChange(
                date
                  ? combineLocalDateTime(localDatePart(date), timePart(value))
                  : ""
              )
            }
          />
        </PopoverContent>
      </Popover>
      <Input
        inputMode="numeric"
        placeholder="00:00"
        value={timePart(value)}
        onChange={(event) =>
          onChange(combineLocalDateTime(selectedDatePart, event.target.value))
        }
      />
    </div>
  )
}

function PatternFields({
  fields,
  onChange,
}: {
  fields: TaskInputFields
  onChange: (next: Partial<TaskInputFields>) => void
}) {
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field
        label="Include crawl"
        hint="Optional URL patterns allowed during crawling, one per line."
      >
        <Textarea
          value={fields.includeCrawl}
          className="min-h-20 resize-y"
          onChange={(event) => onChange({ includeCrawl: event.target.value })}
        />
      </Field>
      <Field
        label="Exclude crawl"
        hint="Optional URL patterns blocked during crawling, one per line."
      >
        <Textarea
          value={fields.excludeCrawl}
          className="min-h-20 resize-y"
          onChange={(event) => onChange({ excludeCrawl: event.target.value })}
        />
      </Field>
      <Field
        label="Include result"
        hint="Optional URL patterns allowed in the final index output, one per line."
      >
        <Textarea
          value={fields.includeResult}
          className="min-h-20 resize-y"
          onChange={(event) => onChange({ includeResult: event.target.value })}
        />
      </Field>
      <Field
        label="Exclude result"
        hint="Optional URL patterns removed from the final index output, one per line."
      >
        <Textarea
          value={fields.excludeResult}
          className="min-h-20 resize-y"
          onChange={(event) => onChange({ excludeResult: event.target.value })}
        />
      </Field>
    </div>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <FieldLabel hint={hint}>{label}</FieldLabel>
      {children}
    </div>
  )
}

function FieldLabel({
  children,
  hint,
}: {
  children: string
  hint: string
}) {
  return (
    <div className="flex items-center gap-2">
      <Label>{children}</Label>
      <InfoTooltip>{hint}</InfoTooltip>
    </div>
  )
}

function InfoTooltip({ children }: { children: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            className="size-5 text-muted-foreground"
            tabIndex={-1}
            type="button"
            variant="ghost"
            size="icon-xs"
          />
        }
      >
        <CircleHelpIcon />
        <span className="sr-only">More information</span>
      </TooltipTrigger>
      <TooltipContent side="top" align="start">
        {children}
      </TooltipContent>
    </Tooltip>
  )
}

function TaskSelect({
  className,
  value,
  options,
  onChange,
  "aria-label": ariaLabel,
}: {
  className?: string
  value: string
  options: Array<{ value: string; label: string }>
  onChange: (value: string) => void
  "aria-label"?: string
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
      <SelectTrigger aria-label={ariaLabel} className={cn("w-36", className)}>
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
