import { useState } from "react"
import type { ReactNode } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
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
  usePreviewCrawlSchedule,
  useUpdateCrawlSchedule,
} from "@/hooks/use-crawl-graphs"
import type {
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

export function ScheduleEditorDialog({
  graphId,
  schedule,
  initialName,
  initialMaxCrawls,
  open,
  onOpenChange,
  onSaved,
}: {
  graphId: string
  schedule: CrawlSchedule | null
  initialName?: string
  initialMaxCrawls?: number
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved?: (schedule: CrawlSchedule) => void
}) {
  const interval = initialInterval(schedule)
  const [name, setName] = useState(schedule?.name ?? initialName ?? "")
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
  const [maxCrawls, setMaxCrawls] = useState(
    (schedule?.max_crawls ?? initialMaxCrawls ?? 1000).toString()
  )
  const [urls, setUrls] = useState(schedule?.urls.join("\n") ?? "")
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

  const payload = (): CrawlScheduleInput => {
    const startUrls = Array.from(
      new Set(
        urls
          .split(/\r?\n/)
          .map((url) => url.trim())
          .filter(Boolean)
      )
    )
    return {
      name: name.trim(),
      enabled: schedule?.enabled ?? true,
      timing: timing(),
      starts_at: instant(startsAt),
      ends_at: instant(endsAt),
      maximum_run_count: maximumRuns ? Number(maximumRuns) : null,
      max_crawls: Number(maxCrawls),
      urls: startUrls,
      overlap_policy: overlap,
      misfire_policy: misfire,
    }
  }

  const save = () => {
    const values = payload()
    if (!values.name || values.urls.length === 0) {
      toast.error("Schedule name and at least one start URL are required.")
      return
    }
    if (!Number.isInteger(values.max_crawls) || values.max_crawls < 1) {
      toast.error("Maximum crawls must be at least one.")
      return
    }
    if (values.max_crawls < values.urls.length) {
      toast.error(
        `Maximum crawls must cover all ${values.urls.length.toLocaleString()} start URLs.`
      )
      return
    }
    const options = {
      onSuccess: (saved: CrawlSchedule) => {
        toast.success(schedule ? "Schedule updated." : "Schedule created.")
        onSaved?.(saved)
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
            Future occurrences use the latest saved plan and these start URLs.
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

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
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
            <Field label="Maximum crawls per run">
              <Input
                type="number"
                min={1}
                max={1_000_000}
                value={maxCrawls}
                onChange={(event) => setMaxCrawls(event.target.value)}
              />
            </Field>
          </div>

          <Field label="Start URLs · one per line">
            <Textarea
              className="min-h-24 font-mono text-xs"
              value={urls}
              placeholder={"https://example.com/\nhttps://example.org/"}
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  )
}
