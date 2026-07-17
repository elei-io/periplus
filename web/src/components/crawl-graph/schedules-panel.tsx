import {
  CalendarClockIcon,
  LoaderCircleIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import type { ReactNode } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateCrawlSchedule,
  useCrawlSchedules,
  useDeleteCrawlSchedule,
  usePreviewCrawlSchedule,
  useRunCrawlScheduleNow,
  useSetCrawlScheduleEnabled,
  useUpdateCrawlSchedule,
} from "@/hooks/use-crawl-graphs"
import type {
  CrawlGraphDetail,
  CrawlSchedule,
  CrawlScheduleInput,
  ScheduleTiming,
} from "@/types/graphs"

type IntervalUnit = "minutes" | "hours" | "days"

const unitSeconds: Record<IntervalUnit, number> = {
  minutes: 60,
  hours: 3600,
  days: 86400,
}

function localInputValue(value: string | null) {
  if (!value) return ""
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 16)
}

function instant(value: string) {
  return value ? new Date(value).toISOString() : null
}

function initialInterval(schedule: CrawlSchedule | null) {
  const seconds =
    schedule?.timing.kind === "interval" ? schedule.timing.seconds : 3600
  if (seconds % 86400 === 0)
    return { amount: seconds / 86400, unit: "days" as const }
  if (seconds % 3600 === 0)
    return { amount: seconds / 3600, unit: "hours" as const }
  return { amount: seconds / 60, unit: "minutes" as const }
}

export function SchedulesPanel({ graph }: { graph: CrawlGraphDetail }) {
  const schedulesQuery = useCrawlSchedules(graph.id)
  const enabledMutation = useSetCrawlScheduleEnabled(graph.id)
  const deleteMutation = useDeleteCrawlSchedule(graph.id)
  const runNow = useRunCrawlScheduleNow(graph.id)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<CrawlSchedule | null>(null)
  const schedules = schedulesQuery.data?.items ?? []

  return (
    <div className="space-y-4 pt-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="font-medium">Schedules</h3>
          <p className="text-sm text-muted-foreground">
            Create recurring runs using the latest saved graph.
          </p>
        </div>
        <Button
          disabled={!graph.root_node_id}
          onClick={() => setCreating(true)}
        >
          <PlusIcon />
          Add schedule
        </Button>
      </div>

      {schedulesQuery.isLoading ? (
        <LoaderCircleIcon className="mx-auto my-10 size-5 animate-spin text-muted-foreground" />
      ) : schedules.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {schedules.map((schedule) => (
            <Card key={schedule.id}>
              <CardHeader className="gap-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="truncate">{schedule.name}</CardTitle>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {timingLabel(schedule.timing)}
                    </p>
                  </div>
                  <Badge variant={schedule.status === "active" ? "secondary" : "outline"}>
                    {schedule.status.replaceAll("_", " ")}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">Next run</dt>
                    <dd>{formatDate(schedule.next_run_at)}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Runs</dt>
                    <dd>
                      {schedule.run_count} /{" "}
                      {schedule.maximum_run_count ?? "unlimited"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Root URLs</dt>
                    <dd>{schedule.root_urls.length}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">Last run</dt>
                    <dd>{formatDate(schedule.last_occurrence_at)}</dd>
                  </div>
                </dl>
                {schedule.last_error ? (
                  <p className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                    {schedule.last_error}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={runNow.isPending}
                    onClick={() =>
                      runNow.mutate(schedule.id, {
                        onSuccess: () =>
                          toast.success("Manual graph run queued."),
                      })
                    }
                  >
                    <PlayIcon />
                    Run now
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={
                      enabledMutation.isPending ||
                      schedule.status === "exhausted" ||
                      schedule.status === "ended"
                    }
                    onClick={() =>
                      enabledMutation.mutate({
                        scheduleId: schedule.id,
                        enabled: !schedule.enabled,
                      })
                    }
                  >
                    {schedule.enabled ? <PauseIcon /> : <PlayIcon />}
                    {schedule.enabled ? "Pause" : "Resume"}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setEditing(schedule)}
                  >
                    <PencilIcon />
                    Edit
                  </Button>
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    disabled={deleteMutation.isPending}
                    onClick={() => {
                      if (window.confirm(`Delete schedule “${schedule.name}”?`))
                        deleteMutation.mutate(schedule.id)
                    }}
                  >
                    <Trash2Icon />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed px-6 py-12 text-center">
          <CalendarClockIcon className="mx-auto size-6 text-muted-foreground" />
          <p className="mt-3 text-sm font-medium">No schedules yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Add an interval or cron schedule to run this graph automatically.
          </p>
        </div>
      )}

      {creating ? (
        <ScheduleEditorDialog
          key="create"
          graphId={graph.id}
          schedule={null}
          open
          onOpenChange={setCreating}
        />
      ) : null}
      {editing ? (
        <ScheduleEditorDialog
          key={editing.id}
          graphId={graph.id}
          schedule={editing}
          open
          onOpenChange={(open) => !open && setEditing(null)}
        />
      ) : null}
    </div>
  )
}

function ScheduleEditorDialog({
  graphId,
  schedule,
  open,
  onOpenChange,
}: {
  graphId: string
  schedule: CrawlSchedule | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const interval = initialInterval(schedule)
  const [name, setName] = useState(schedule?.name ?? "")
  const [kind, setKind] = useState<"interval" | "cron">(
    schedule?.timing.kind ?? "interval"
  )
  const [amount, setAmount] = useState(interval.amount)
  const [unit, setUnit] = useState<IntervalUnit>(interval.unit)
  const [cron, setCron] = useState(
    schedule?.timing.kind === "cron" ? schedule.timing.expression : "0 6 * * *"
  )
  const [timezone, setTimezone] = useState(
    schedule?.timing.kind === "cron"
      ? schedule.timing.timezone
      : Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  )
  const [startsAt, setStartsAt] = useState(
    localInputValue(schedule?.starts_at ?? null)
  )
  const [endsAt, setEndsAt] = useState(
    localInputValue(schedule?.ends_at ?? null)
  )
  const [maximumRuns, setMaximumRuns] = useState(
    schedule?.maximum_run_count?.toString() ?? ""
  )
  const [urls, setUrls] = useState(schedule?.root_urls.join("\n") ?? "")
  const [overlap, setOverlap] = useState<"skip" | "allow">(
    schedule?.overlap_policy ?? "skip"
  )
  const [misfire, setMisfire] = useState<"skip" | "run_once">(
    schedule?.misfire_policy ?? "skip"
  )
  const createMutation = useCreateCrawlSchedule(graphId)
  const updateMutation = useUpdateCrawlSchedule(graphId)
  const previewMutation = usePreviewCrawlSchedule(graphId)

  const timing = (): ScheduleTiming =>
    kind === "interval"
      ? {
          kind,
          seconds: Math.max(1, amount) * unitSeconds[unit],
        }
      : { kind, expression: cron.trim(), timezone: timezone.trim() }

  const payload = (): CrawlScheduleInput => ({
    name: name.trim(),
    enabled: schedule?.enabled ?? true,
    timing: timing(),
    starts_at: instant(startsAt),
    ends_at: instant(endsAt),
    maximum_run_count: maximumRuns ? Number(maximumRuns) : null,
    root_urls: urls
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean),
    overlap_policy: overlap,
    misfire_policy: misfire,
  })

  const save = () => {
    const values = payload()
    if (!values.name || values.root_urls.length === 0) {
      toast.error("Schedule name and at least one root URL are required.")
      return
    }
    const options = {
      onSuccess: () => {
        toast.success(schedule ? "Schedule updated." : "Schedule created.")
        onOpenChange(false)
      },
    }
    if (schedule) {
      updateMutation.mutate(
        { scheduleId: schedule.id, payload: values },
        options
      )
    } else {
      createMutation.mutate(values, options)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {schedule ? "Edit schedule" : "Create schedule"}
          </DialogTitle>
          <DialogDescription>
            Future occurrences use the latest saved graph and these root URLs.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-5">
          <Field label="Name">
            <Input
              value={name}
              placeholder="Daily catalogue crawl"
              onChange={(event) => setName(event.target.value)}
            />
          </Field>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Timing">
              <Select
                value={kind}
                onValueChange={(value) =>
                  value && setKind(value as "interval" | "cron")
                }
              >
                <SelectTrigger className="w-full">
                  <span>{kind === "interval" ? "Interval" : "Cron"}</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="interval">Interval</SelectItem>
                  <SelectItem value="cron">Cron</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            {kind === "interval" ? (
              <Field label="Every">
                <div className="grid grid-cols-[1fr_8rem] gap-2">
                  <Input
                    type="number"
                    min={1}
                    value={amount}
                    onChange={(event) => setAmount(Number(event.target.value))}
                  />
                  <Select
                    value={unit}
                    onValueChange={(value) =>
                      value && setUnit(value as IntervalUnit)
                    }
                  >
                    <SelectTrigger className="w-full">
                      <span>{unit}</span>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="minutes">Minutes</SelectItem>
                      <SelectItem value="hours">Hours</SelectItem>
                      <SelectItem value="days">Days</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </Field>
            ) : (
              <>
                <Field label="Cron expression">
                  <Input
                    className="font-mono"
                    value={cron}
                    onChange={(event) => setCron(event.target.value)}
                  />
                </Field>
                <Field label="Timezone">
                  <Input
                    value={timezone}
                    placeholder="Asia/Tokyo"
                    onChange={(event) => setTimezone(event.target.value)}
                  />
                </Field>
              </>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Starts at · optional">
              <Input
                type="datetime-local"
                value={startsAt}
                onChange={(event) => setStartsAt(event.target.value)}
              />
            </Field>
            <Field label="Ends at · optional">
              <Input
                type="datetime-local"
                value={endsAt}
                onChange={(event) => setEndsAt(event.target.value)}
              />
            </Field>
            <Field label="Maximum runs · optional">
              <Input
                type="number"
                min={1}
                value={maximumRuns}
                onChange={(event) => setMaximumRuns(event.target.value)}
              />
            </Field>
          </div>

          <Field label="Root URLs · one per line">
            <Textarea
              className="min-h-32 font-mono text-xs"
              value={urls}
              placeholder={"https://example.com/\nhttps://example.com/catalogue"}
              onChange={(event) => setUrls(event.target.value)}
            />
          </Field>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="If the previous run is active">
              <Select
                value={overlap}
                onValueChange={(value) =>
                  value && setOverlap(value as "skip" | "allow")
                }
              >
                <SelectTrigger className="w-full">
                  <span>
                    {overlap === "skip"
                      ? "Skip occurrence"
                      : "Start another run"}
                  </span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip">Skip occurrence</SelectItem>
                  <SelectItem value="allow">Start another run</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field label="If Atlas missed occurrences">
              <Select
                value={misfire}
                onValueChange={(value) =>
                  value && setMisfire(value as "skip" | "run_once")
                }
              >
                <SelectTrigger className="w-full">
                  <span>
                    {misfire === "skip" ? "Skip missed runs" : "Run once"}
                  </span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip">Skip missed runs</SelectItem>
                  <SelectItem value="run_once">Run once</SelectItem>
                </SelectContent>
              </Select>
            </Field>
          </div>

          <div className="rounded-md border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs font-medium">Upcoming occurrences</p>
              <Button
                size="sm"
                variant="outline"
                disabled={previewMutation.isPending}
                onClick={() =>
                  previewMutation.mutate({
                    timing: timing(),
                    starts_at: instant(startsAt),
                    ends_at: instant(endsAt),
                    count: 5,
                  })
                }
              >
                Preview
              </Button>
            </div>
            <div className="mt-2 space-y-1 text-xs text-muted-foreground">
              {previewMutation.data?.occurrences.map((value) => (
                <p key={value}>{new Date(value).toLocaleString()}</p>
              )) ?? <p>Preview the next five scheduled times.</p>}
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button
            disabled={createMutation.isPending || updateMutation.isPending}
            onClick={save}
          >
            {schedule ? "Save changes" : "Create schedule"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  )
}

function timingLabel(timing: ScheduleTiming) {
  if (timing.kind === "cron")
    return `${timing.expression} · ${timing.timezone}`
  const seconds = timing.seconds
  if (seconds % 86400 === 0)
    return `Every ${seconds / 86400} day${seconds === 86400 ? "" : "s"}`
  if (seconds % 3600 === 0)
    return `Every ${seconds / 3600} hour${seconds === 3600 ? "" : "s"}`
  return `Every ${seconds / 60} minute${seconds === 60 ? "" : "s"}`
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString() : "—"
}
