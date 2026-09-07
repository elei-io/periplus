import {
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  Settings2Icon,
  TrashIcon,
} from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  useFrontierControls,
  useReplaceFrontierControls,
} from "@/hooks/use-frontier"
import { extractApiError } from "@/lib/api"
import {
  settingFields,
  settingsDraft,
  settingsPayload,
  type FrontierDraft,
} from "./settings-form"

const reasons: Record<string, string> = {
  crawler_paused: "New captures are paused.",
  attempt_allowance_exhausted: "The attempt allowance is exhausted.",
  capture_time_allowance_exhausted:
    "The remaining time allowance cannot cover another capture.",
  dispatch_capacity: "All dispatch slots are occupied.",
  dispatch_rate: "Waiting for the configured dispatch interval.",
  background_disabled: "Background exploration is disabled.",
  background_attempt_allowance_exhausted:
    "The background attempt allowance is exhausted.",
  background_capture_time_allowance_exhausted:
    "The remaining background time allowance cannot cover another capture.",
  retained_acquisition_capacity:
    "Retained acquisition capacity is full; new acquisition admission is waiting.",
}
const reason = (value: string | null) =>
  value ? (reasons[value] ?? value.replaceAll("_", " ")) : null
const time = (milliseconds: number) =>
  `${(milliseconds / 3600000).toLocaleString(undefined, { maximumFractionDigits: 2 })} h`

export function FrontierControlsPage() {
  const query = useFrontierControls()
  const replace = useReplaceFrontierControls()
  const [draft, setDraft] = useState<FrontierDraft | null>(null)
  const state = query.data
  if (!state)
    return (
      <div
        className="grid w-full place-content-center gap-3 text-sm"
        role="status"
      >
        <p>
          {query.isError
            ? `Crawler status unavailable: ${extractApiError(query.error)}`
            : "Loading crawler controls…"}
        </p>
        {query.isError && (
          <Button variant="outline" onClick={() => void query.refetch()}>
            Retry
          </Button>
        )}
      </div>
    )
  const staleDraft = draft !== null && draft.version !== state.policy_version
  const save = () => {
    if (!draft) return
    try {
      replace.mutate(settingsPayload(draft), {
        onSuccess: () => setDraft(null),
      })
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }
  return (
    <div className="flex w-full max-w-6xl flex-col gap-6">
      <section className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">
              Crawler controls
            </h1>
            <Badge variant={state.settings.paused ? "outline" : "secondary"}>
              {state.settings.paused ? "Paused" : "Dispatch enabled"}
            </Badge>
          </div>
          <p className="max-w-2xl text-sm text-muted-foreground">
            One crawler, shared across collection requests and background
            exploration. Started captures finish when you pause.
          </p>
          <p className="text-xs text-muted-foreground">
            As of {new Date(state.as_of).toLocaleTimeString()} · Settings v
            {state.policy_version}
            {state.updated_by ? ` · ${state.updated_by}` : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="icon"
            aria-label="Refresh crawler status"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RefreshCwIcon />
          </Button>
          <Button
            variant="outline"
            disabled={replace.isPending || query.isError || draft !== null}
            onClick={() => setDraft(settingsDraft(state))}
          >
            <Settings2Icon />
            Edit settings
          </Button>
          <Button
            disabled={replace.isPending || query.isError || draft !== null}
            onClick={() =>
              replace.mutate({
                expected_version: state.policy_version,
                settings: { ...state.settings, paused: !state.settings.paused },
              })
            }
          >
            {state.settings.paused ? <PlayIcon /> : <PauseIcon />}
            {state.settings.paused ? "Resume dispatch" : "Pause new captures"}
          </Button>
        </div>
      </section>
      {query.isError && (
        <div
          role="alert"
          className="rounded-md border border-destructive p-3 text-sm"
        >
          Status may be stale. Last successful refresh:{" "}
          {new Date(query.dataUpdatedAt).toLocaleTimeString()}.{" "}
          {extractApiError(query.error)}
        </div>
      )}
      {(state.dispatch_waiting_reason ||
        state.acquisition_admission_waiting_reason) && (
        <div
          role="status"
          className="rounded-md border bg-muted/50 p-3 text-sm"
        >
          {reason(state.dispatch_waiting_reason)}{" "}
          {reason(state.acquisition_admission_waiting_reason)}
          {state.next_rate_eligibility_at && (
            <span className="block text-xs text-muted-foreground">
              Rate eligibility:{" "}
              {new Date(state.next_rate_eligibility_at).toLocaleTimeString()}.
              This is not a promised start time.
            </span>
          )}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Metric
          label="Pending acquisitions"
          value={state.pending_acquisitions.toLocaleString()}
          detail={`Admission limit ${state.settings.admission_limit.toLocaleString()}`}
        />
        <Metric
          label="Dispatched acquisitions"
          value={state.dispatched_acquisitions.toLocaleString()}
          detail={`Up to ${state.settings.dispatch_limit.toLocaleString()} concurrent dispatches`}
        />
        <Metric
          label="Configured dispatch pace"
          value={
            state.settings.captures_per_minute === null
              ? "Unlimited"
              : `${state.settings.captures_per_minute}/min`
          }
          detail="Upper bound; actual throughput depends on eligibility and capacity"
        />
        <Metric
          label="Background allocation"
          value={`${state.settings.background_share}%`}
          detail={
            reason(state.background_waiting_reason) ??
            "Share when both lanes are eligible; spare capacity may also be used"
          }
        />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Physical attempt allowance</CardTitle>
            <CardDescription>
              Cumulative across requests and background work. Sharing does not
              multiply physical attempts.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-2xl font-semibold tabular-nums">
              {state.started_attempts.toLocaleString()}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                started / {state.settings.attempt_allowance.toLocaleString()}{" "}
                allowed
              </span>
            </p>
            <p>
              {state.reserved_attempts.toLocaleString()} reserved ·{" "}
              {Math.max(
                0,
                state.settings.attempt_allowance -
                  state.started_attempts -
                  state.reserved_attempts
              ).toLocaleString()}{" "}
              unreserved
            </p>
            <p className="text-xs text-muted-foreground">
              Background: {state.background_started_attempts.toLocaleString()}{" "}
              started + {state.background_reserved_attempts.toLocaleString()}{" "}
              reserved /{" "}
              {state.settings.background_attempt_allowance.toLocaleString()}{" "}
              allowed
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Capture time allowance</CardTitle>
            <CardDescription>
              Measured client time, or the reserved bound when usage is unknown.
              Provider billing is separate.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-2xl font-semibold tabular-nums">
              {time(state.charged_capture_ms)}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                accounted / {time(state.settings.capture_time_allowance_ms)}{" "}
                allowed
              </span>
            </p>
            <p>{time(state.reserved_capture_ms)} reserved</p>
            <p className="text-xs text-muted-foreground">
              Background: {time(state.background_charged_capture_ms)} accounted
              + {time(state.background_reserved_capture_ms)} reserved /{" "}
              {time(state.settings.background_capture_time_allowance_ms)}{" "}
              allowed
            </p>
          </CardContent>
        </Card>
      </div>
      <p className="text-xs text-muted-foreground">
        Retained acquisitions: {state.retained_acquisitions.toLocaleString()} /{" "}
        {state.settings.acquisition_limit.toLocaleString()} · Collection URL
        records: {state.retained_interests.toLocaleString()} /{" "}
        {state.settings.interest_limit.toLocaleString()}. Allowances remain
        cumulative until an operator increases their limits.
      </p>
      {draft && (
        <Card>
          <CardHeader>
            <CardTitle>Edit crawler settings</CardTitle>
            <CardDescription>
              Applies to queued and future work. Started captures retain their
              authorized settings.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="space-y-6"
              onSubmit={(event) => {
                event.preventDefault()
                save()
              }}
            >
              {staleDraft && (
                <div
                  role="alert"
                  className="space-y-2 rounded-md border border-destructive p-3 text-sm"
                >
                  <p>
                    Settings changed while you were editing. Reload the latest
                    settings and review your changes.
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setDraft(settingsDraft(state))}
                  >
                    Reload latest settings
                  </Button>
                </div>
              )}
              <div className="flex items-center gap-3">
                <Switch
                  id="crawler-paused"
                  checked={draft.settings.paused}
                  onCheckedChange={(paused) =>
                    setDraft({
                      ...draft,
                      settings: { ...draft.settings, paused },
                    })
                  }
                />
                <Label htmlFor="crawler-paused">Pause new captures</Label>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="grid gap-2">
                  <Label htmlFor="dispatch-rate">Dispatches per minute</Label>
                  <Input
                    id="dispatch-rate"
                    type="number"
                    min={1}
                    max={60000}
                    disabled={draft.unlimitedRate}
                    value={draft.rate}
                    onChange={(event) =>
                      setDraft({ ...draft, rate: event.target.value })
                    }
                  />
                </div>
                <div className="flex items-center gap-3">
                  <Switch
                    id="unlimited-rate"
                    checked={draft.unlimitedRate}
                    onCheckedChange={(unlimitedRate) =>
                      setDraft({ ...draft, unlimitedRate })
                    }
                  />
                  <Label htmlFor="unlimited-rate">
                    No global dispatch rate limit
                  </Label>
                </div>
              </div>
              {(["pace", "budget", "capacity"] as const).map((group) => (
                <fieldset key={group} className="space-y-3">
                  <legend className="text-sm font-medium">
                    {group === "pace"
                      ? "Pace and background work"
                      : group === "budget"
                        ? "Cumulative operating allowances"
                        : "Retention and admission capacity"}
                  </legend>
                  {group === "pace" && (
                    <p className="text-xs text-muted-foreground">
                      Set background allocation to zero to stop background work.
                      A positive allocation can use spare capacity when
                      collections are idle.
                    </p>
                  )}
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {settingFields
                      .filter((field) => field.group === group)
                      .map((field) => (
                        <div key={field.key} className="grid gap-2">
                          <Label htmlFor={field.key}>{field.label}</Label>
                          <Input
                            id={field.key}
                            type="number"
                            min={field.min / field.unit}
                            max={field.max / field.unit}
                            step={field.unit === 1 ? 1 : "any"}
                            value={draft.numbers[field.key]}
                            onChange={(event) =>
                              setDraft({
                                ...draft,
                                numbers: {
                                  ...draft.numbers,
                                  [field.key]: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                      ))}
                  </div>
                </fieldset>
              ))}
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium">
                  Global exclusions
                </legend>
                <p className="text-xs text-muted-foreground">
                  Apply to collections and background exploration. Use an exact
                  host, *.example.com, or *. Paths include descendants.
                </p>
                {draft.settings.exclusions.map((rule, index) => (
                  <div key={index} className="flex gap-2">
                    <Input
                      aria-label={`Excluded host ${index + 1}`}
                      placeholder="*.example.com"
                      value={rule.host}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          settings: {
                            ...draft.settings,
                            exclusions: draft.settings.exclusions.map(
                              (item, position) =>
                                position === index
                                  ? { ...item, host: event.target.value }
                                  : item
                            ),
                          },
                        })
                      }
                    />
                    <Input
                      aria-label={`Excluded path ${index + 1}`}
                      placeholder="/private"
                      value={rule.path_prefix}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          settings: {
                            ...draft.settings,
                            exclusions: draft.settings.exclusions.map(
                              (item, position) =>
                                position === index
                                  ? { ...item, path_prefix: event.target.value }
                                  : item
                            ),
                          },
                        })
                      }
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={`Remove exclusion ${index + 1}`}
                      onClick={() =>
                        setDraft({
                          ...draft,
                          settings: {
                            ...draft.settings,
                            exclusions: draft.settings.exclusions.filter(
                              (_, position) => position !== index
                            ),
                          },
                        })
                      }
                    >
                      <TrashIcon />
                    </Button>
                  </div>
                ))}
                <Button
                  type="button"
                  variant="outline"
                  disabled={draft.settings.exclusions.length >= 100}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      settings: {
                        ...draft.settings,
                        exclusions: [
                          ...draft.settings.exclusions,
                          { host: "", path_prefix: "/" },
                        ],
                      },
                    })
                  }
                >
                  <PlusIcon />
                  Add exclusion
                </Button>
              </fieldset>
              <div className="flex gap-2">
                <Button
                  type="submit"
                  disabled={replace.isPending || staleDraft || query.isError}
                >
                  Save settings
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={replace.isPending}
                  onClick={() => setDraft(null)}
                >
                  Discard changes
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{label}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
        <p className="text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  )
}
