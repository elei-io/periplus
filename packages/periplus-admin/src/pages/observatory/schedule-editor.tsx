import { useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { useScheduleAction } from "@/hooks/use-schedules"
import { extractApiError } from "@/lib/api"
import type { RequestDefinition, RequestSchedule } from "@/types/schedules"
function local(value: string) {
  const d = new Date(value)
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16)
}
export function ScheduleEditor({
  definition,
  schedule,
  close,
}: {
  definition: RequestDefinition
  schedule?: RequestSchedule
  close: () => void
}) {
  const action = useScheduleAction()
  const [defaultStart] = useState(() => new Date(Date.now() + 60000).toISOString())
  const config = schedule?.configuration
  const [kind, setKind] = useState<"interval" | "cron">(
    config?.kind ?? "interval"
  )
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {schedule ? "Edit" : "Add"} schedule · {definition.name}
        </CardTitle>
        <CardDescription>
          Start/stop use your local time (
          {Intl.DateTimeFormat().resolvedOptions().timeZone}). Cron is evaluated
          in the explicit timezone below. Start is inclusive; stop is exclusive.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            try {
              const form = new FormData(event.currentTarget)
              const body = {
                kind,
                interval_seconds:
                  kind === "interval" ? Number(form.get("interval")) : null,
                cron: kind === "cron" ? String(form.get("cron")) : null,
                timezone: String(form.get("timezone")),
                start_at: new Date(String(form.get("start"))).toISOString(),
                stop_at: form.get("stop")
                  ? new Date(String(form.get("stop"))).toISOString()
                  : null,
                max_count: form.get("count") ? Number(form.get("count")) : null,
                enabled: config?.enabled ?? true,
                ...(schedule ? { expected_version: schedule.version } : {}),
              }
              action.mutate(
                {
                  path: `/request-definitions/${definition.id}/schedules${schedule ? `/${schedule.id}` : ""}`,
                  method: schedule ? "PUT" : "POST",
                  body,
                },
                { onSuccess: close }
              )
            } catch (error) {
              toast.error(extractApiError(error))
            }
          }}
        >
          <div className="flex gap-2">
            {(["interval", "cron"] as const).map((value) => (
              <Button
                key={value}
                type="button"
                variant={kind === value ? "secondary" : "outline"}
                aria-pressed={kind === value}
                onClick={() => setKind(value)}
              >
                {value}
              </Button>
            ))}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {kind === "interval" ? (
              <label className="space-y-2">
                Interval (seconds)
                <Input
                  key="interval"
                  name="interval"
                  type="number"
                  min={60}
                  max={31536000}
                  step={1}
                  required
                  defaultValue={config?.interval_seconds ?? 3600}
                />
              </label>
            ) : (
              <label className="space-y-2">
                Cron (minute hour day month weekday)
                <Input
                  key="cron"
                  name="cron"
                  required
                  maxLength={100}
                  defaultValue={config?.cron ?? "0 * * * *"}
                />
              </label>
            )}
            <label className="space-y-2">
              Timezone (IANA)
              <Input
                name="timezone"
                required
                defaultValue={
                  config?.timezone ??
                  Intl.DateTimeFormat().resolvedOptions().timeZone
                }
              />
            </label>
            <label className="space-y-2">
              Start date and time
              <Input
                name="start"
                type="datetime-local"
                required
                defaultValue={local(
                  config?.start_at ?? defaultStart
                )}
              />
            </label>
            <label className="space-y-2">
              Stop date and time (optional)
              <Input
                name="stop"
                type="datetime-local"
                defaultValue={config?.stop_at ? local(config.stop_at) : ""}
              />
            </label>
            <label className="space-y-2">
              Maximum executions (blank means unlimited)
              <Input
                name="count"
                type="number"
                min={1}
                max={1000000}
                step={1}
                defaultValue={config?.max_count ?? ""}
              />
            </label>
          </div>
          <p className="text-xs text-muted-foreground">
            Intervals stay anchored to start. Cron uses numeric five-field
            expressions. Execution counts include requests that fail or are
            cancelled. Pausing the crawler skips scheduled ticks.
          </p>
          <div className="flex gap-2">
            <Button type="submit" disabled={action.isPending}>Save schedule</Button>
            <Button type="button" variant="outline" onClick={close}>
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
