import {
  ArrowLeftIcon,
  CalendarClockIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react"
import { useState, type ReactNode } from "react"
import { toast } from "sonner"

import { ScheduleEditorDialog } from "@/components/crawl-graph/schedule-editor-dialog"
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
  useAllCrawlSchedules,
  useCrawlGraphs,
  useCrawlSchedule,
  useDeleteCrawlSchedule,
  useRunCrawlScheduleNow,
  useSetCrawlScheduleEnabled,
} from "@/hooks/use-crawl-graphs"
import type { CrawlScheduleResource, ScheduleTiming } from "@/types/graphs"

export function CrawlSchedulesPage({
  onNavigate,
}: {
  onNavigate: (href: string) => void
}) {
  const schedulesQuery = useAllCrawlSchedules()
  const plansQuery = useCrawlGraphs()
  const [selectingPlan, setSelectingPlan] = useState(false)
  const [planId, setPlanId] = useState("")
  const [creatingForPlan, setCreatingForPlan] = useState<string | null>(null)
  const schedules = schedulesQuery.data?.items ?? []
  const plans = plansQuery.data?.items.filter((plan) => plan.root_node_id) ?? []

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <Badge variant="outline">{schedulesQuery.data?.total ?? 0} total</Badge>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={schedulesQuery.isFetching}
            onClick={() => void schedulesQuery.refetch()}
          >
            <RefreshCwIcon
              className={schedulesQuery.isFetching ? "animate-spin" : ""}
            />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setSelectingPlan(true)}>
            <PlusIcon />
            New schedule
          </Button>
        </div>
      </section>

      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Schedule</TableHead>
            <TableHead>Plan</TableHead>
            <TableHead>Timing</TableHead>
            <TableHead>Next run</TableHead>
            <TableHead>Runs</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {schedules.map((schedule) => (
            <TableRow
              key={schedule.id}
              className="cursor-pointer"
              onClick={() => onNavigate(`/crawls/schedules/${schedule.id}`)}
            >
              <TableCell>
                <span className="font-medium">{schedule.name}</span>
                <span className="block text-xs text-muted-foreground">
                  {schedule.urls.length.toLocaleString()}{" "}
                  {schedule.urls.length === 1 ? "start URL" : "start URLs"}
                </span>
              </TableCell>
              <TableCell className="font-mono text-xs">
                {schedule.plan_slug}
              </TableCell>
              <TableCell>{timingLabel(schedule.timing)}</TableCell>
              <TableCell>{formatDate(schedule.next_run_at)}</TableCell>
              <TableCell>
                {schedule.run_count} / {schedule.maximum_run_count ?? "∞"}
              </TableCell>
              <TableCell>
                <ScheduleStatus schedule={schedule} />
              </TableCell>
            </TableRow>
          ))}
          {!schedulesQuery.isLoading && schedules.length === 0 ? (
            <TableRow>
              <TableCell colSpan={6} className="h-32 text-center">
                No schedules yet
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>

      <Dialog open={selectingPlan} onOpenChange={setSelectingPlan}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Choose a crawl plan</DialogTitle>
            <DialogDescription>
              Each scheduled run offers its start URLs to this plan’s root node.
            </DialogDescription>
          </DialogHeader>
          <Select
            value={planId || null}
            onValueChange={(value) => value && setPlanId(value)}
          >
            <SelectTrigger className="w-full">
              <span>
                {plans.find((plan) => plan.id === planId)?.slug ??
                  "Select plan"}
              </span>
            </SelectTrigger>
            <SelectContent>
              {plans.map((plan) => (
                <SelectItem key={plan.id} value={plan.id}>
                  {plan.slug}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {plans.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Create a plan with a root node before adding a schedule.
            </p>
          ) : null}
          <DialogFooter showCloseButton>
            <Button
              disabled={!planId}
              onClick={() => {
                setSelectingPlan(false)
                setCreatingForPlan(planId)
              }}
            >
              Continue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {creatingForPlan ? (
        <ScheduleEditorDialog
          key={creatingForPlan}
          graphId={creatingForPlan}
          schedule={null}
          open
          onOpenChange={(open) => !open && setCreatingForPlan(null)}
        />
      ) : null}
    </div>
  )
}

export function CrawlScheduleDetailPage({
  scheduleId,
  onNavigate,
}: {
  scheduleId: string
  onNavigate: (href: string) => void
}) {
  const query = useCrawlSchedule(scheduleId)
  const schedule = query.data
  if (query.isLoading) return <Centered>Loading schedule…</Centered>
  if (!schedule) return <Centered>Schedule not found.</Centered>
  return (
    <ScheduleDetail
      key={schedule.updated_at}
      schedule={schedule}
      onNavigate={onNavigate}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
    />
  )
}

function ScheduleDetail({
  schedule,
  onNavigate,
  onRefresh,
  refreshing,
}: {
  schedule: CrawlScheduleResource
  onNavigate: (href: string) => void
  onRefresh: () => void
  refreshing: boolean
}) {
  const enabledMutation = useSetCrawlScheduleEnabled(schedule.plan_id)
  const deleteMutation = useDeleteCrawlSchedule(schedule.plan_id)
  const runNow = useRunCrawlScheduleNow(schedule.plan_id)
  const [editing, setEditing] = useState(false)

  const remove = () => {
    if (!window.confirm(`Delete schedule “${schedule.name}”?`)) return
    deleteMutation.mutate(schedule.id, {
      onSuccess: () => onNavigate("/crawls/schedules"),
    })
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-5">
      <section className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
        <div className="flex min-w-0 items-start gap-3">
          <Button
            size="icon-sm"
            variant="ghost"
            onClick={() => onNavigate("/crawls/schedules")}
          >
            <ArrowLeftIcon />
          </Button>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <CalendarClockIcon className="size-4 text-muted-foreground" />
              <h1 className="truncate text-lg font-medium">{schedule.name}</h1>
              <ScheduleStatus schedule={schedule} />
            </div>
            <button
              type="button"
              className="mt-1 text-sm text-link hover:underline"
              onClick={() => onNavigate(`/crawls/plans/${schedule.plan_id}`)}
            >
              Plan: {schedule.plan_slug}
            </button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={runNow.isPending}
            onClick={() =>
              runNow.mutate(schedule.id, {
                onSuccess: () => toast.success("Manual crawl run queued."),
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
          <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
            <PencilIcon />
            Edit
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={refreshing}
            onClick={onRefresh}
          >
            <RefreshCwIcon className={refreshing ? "animate-spin" : ""} />
            Refresh
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={deleteMutation.isPending}
            onClick={remove}
          >
            <Trash2Icon />
            Delete
          </Button>
        </div>
      </section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Schedule</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid gap-5 text-sm sm:grid-cols-2">
              <Fact label="Timing" value={timingLabel(schedule.timing)} />
              <Fact label="Next run" value={formatDate(schedule.next_run_at)} />
              <Fact
                label="Active window"
                value={`${formatDate(schedule.starts_at)} → ${formatDate(schedule.ends_at)}`}
              />
              <Fact
                label="Run count"
                value={`${schedule.run_count} / ${schedule.maximum_run_count ?? "unlimited"}`}
              />
              <Fact
                label="Crawl budget"
                value={`${schedule.max_crawls.toLocaleString()} per run`}
              />
              <Fact
                label="Overlap"
                value={
                  schedule.overlap_policy === "skip"
                    ? "Skip while a prior run is active"
                    : "Allow concurrent runs"
                }
              />
              <Fact
                label="Missed occurrences"
                value={
                  schedule.misfire_policy === "skip"
                    ? "Skip missed runs"
                    : "Run once after recovery"
                }
              />
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="space-y-4 text-sm">
              <Fact
                label="Last occurrence"
                value={formatDate(schedule.last_occurrence_at)}
              />
              <Fact label="Last run" value={schedule.last_run_id ?? "—"} />
            </dl>
            {schedule.last_error ? (
              <p className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
                {schedule.last_error}
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Start URLs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {schedule.urls.map((url) => (
            <div
              key={url}
              className="rounded-md border bg-muted/20 px-3 py-2 font-mono text-xs break-all"
            >
              {url}
            </div>
          ))}
        </CardContent>
      </Card>

      {editing ? (
        <ScheduleEditorDialog
          key={schedule.updated_at}
          graphId={schedule.plan_id}
          schedule={schedule}
          open
          onOpenChange={setEditing}
        />
      ) : null}
    </div>
  )
}

function ScheduleStatus({
  schedule,
}: {
  schedule: Pick<CrawlScheduleResource, "status">
}) {
  return (
    <Badge variant={schedule.status === "active" ? "secondary" : "outline"}>
      {schedule.status.replaceAll("_", " ")}
    </Badge>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-words">{value}</dd>
    </div>
  )
}

function timingLabel(timing: ScheduleTiming) {
  if (timing.kind === "cron") return `${timing.expression} · ${timing.timezone}`
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

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}
