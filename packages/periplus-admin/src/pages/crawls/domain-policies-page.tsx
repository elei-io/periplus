import { PlusIcon } from "lucide-react"
import { useId, useState } from "react"
import { toast } from "sonner"

import {
  ResourcePagination,
  RESOURCE_PAGE_SIZE,
} from "@/components/resources/resource-pagination"
import { Badge } from "@/components/ui/badge"
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
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useCreateDomainPolicy,
  useDomainPolicies,
  useUpdateDomainPolicy,
} from "@/hooks/use-resource-data"
import { extractApiError } from "@/lib/api"
import type { DomainPolicyRecord } from "@/types/resources"

type Draft = {
  paused: boolean
  host: string
  concurrency: string
  interval: string
}

type EditableDraft = Draft & {
  version: number
  enabled: boolean
}

type HostResolution = {
  host: string
  error: string | null
}

export function DomainPoliciesPage() {
  const [offset, setOffset] = useState(0)
  const [draft, setDraft] = useState<Draft | null>(null)
  const query = useDomainPolicies({ limit: RESOURCE_PAGE_SIZE, offset })

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex items-center justify-between gap-3 border-b pb-4">
        <Badge variant="outline">
          {query.data ? `${query.data.total} total` : "Loading domains…"}
        </Badge>
        <Button
          size="sm"
          onClick={() =>
            setDraft({
              host: "",
              concurrency: "4",
              interval: "0",
              paused: false,
            })
          }
        >
          <PlusIcon />
          New policy
        </Button>
      </section>
      {query.isError && (
        <div
          role="alert"
          className="rounded-md border border-destructive p-3 text-sm"
        >
          Domain status may be stale. {extractApiError(query.error)}{" "}
          <Button variant="outline" onClick={() => void query.refetch()}>
            Retry
          </Button>
        </div>
      )}
      <p className="text-sm text-muted-foreground">
        Current policies govern queued captures at dispatch and start. Paused
        domains wait; started captures finish. Exact hosts override matching
        wildcard policies.
      </p>
      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Host</TableHead>
            <TableHead>Maximum concurrency</TableHead>
            <TableHead>Minimum interval</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {(query.data?.items ?? []).map((policy) => (
            <DomainRow key={policy.id} policy={policy} />
          ))}
        </TableBody>
      </Table>
      <ResourcePagination
        total={query.data?.total ?? 0}
        limit={query.data?.limit ?? RESOURCE_PAGE_SIZE}
        offset={query.data?.offset ?? offset}
        isFetching={query.isFetching}
        onOffsetChange={setOffset}
      />
      <NewDomainPolicy draft={draft} setDraft={setDraft} />
    </div>
  )
}

function DomainRow({ policy }: { policy: DomainPolicyRecord }) {
  const concurrencyId = useId()
  const intervalId = useId()
  const update = useUpdateDomainPolicy(policy.id)
  const fallback = policy.slug === "default-domain"
  const [draft, setDraft] = useState<EditableDraft | null>(null)

  const save = () => {
    if (!draft) return
    const host = resolveHostInput(draft.host)
    const concurrency = Number(draft.concurrency)
    const interval = Number(draft.interval)
    if (!fallback && host.error) return toast.error(host.error)
    if (
      !Number.isInteger(concurrency) ||
      concurrency < 1 ||
      concurrency > 10000
    ) {
      return toast.error(
        "Maximum concurrency must be a whole number between 1 and 10000."
      )
    }
    if (!Number.isFinite(interval) || interval < 0 || interval > 3600) {
      return toast.error("Minimum interval must be between 0 and 3600 seconds.")
    }
    update.mutate(
      {
        expected_version: draft.version,
        paused: draft.paused,
        host_match: fallback ? undefined : host.host,
        maximum_concurrency: concurrency,
        minimum_request_interval_seconds: interval,
        enabled: fallback ? true : draft.enabled,
      },
      { onSuccess: () => setDraft(null) }
    )
  }

  return (
    <>
      <TableRow>
        <TableCell>
          <span className="font-medium">
            {policy.host_match === "*" ? "Every host" : policy.host_match}
          </span>
          <span className="block text-xs text-muted-foreground">
            v{policy.version} · {policy.updated_by}
          </span>
          {fallback ? (
            <span className="block text-xs text-muted-foreground">
              Required fallback
            </span>
          ) : null}
        </TableCell>
        <TableCell>{policy.maximum_concurrency}</TableCell>
        <TableCell>
          {policy.minimum_request_interval_seconds
            ? `${policy.minimum_request_interval_seconds}s`
            : "No delay"}
        </TableCell>
        <TableCell>
          <Badge variant={policy.enabled ? "secondary" : "destructive"}>
            {!policy.enabled
              ? "Disabled"
              : policy.paused
                ? "Paused"
                : "Enabled"}
          </Badge>
        </TableCell>
        <TableCell>
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              setDraft({
                version: policy.version,
                paused: policy.paused,
                host: policy.host_match,
                concurrency: String(policy.maximum_concurrency),
                interval: String(policy.minimum_request_interval_seconds),
                enabled: policy.enabled,
              })
            }
          >
            Edit
          </Button>
        </TableCell>
      </TableRow>
      <Dialog
        open={draft !== null}
        onOpenChange={(open) => {
          if (!open) setDraft(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit domain policy</DialogTitle>
            <DialogDescription>
              Changes apply before queued captures start. Started captures keep
              their authorized settings.
            </DialogDescription>
          </DialogHeader>
          {draft ? (
            <div className="grid gap-4">
              {draft.version !== policy.version && (
                <div
                  role="alert"
                  className="space-y-2 rounded-md border border-destructive p-3 text-sm"
                >
                  <p>
                    This policy changed while you were editing. Reload and
                    review before saving.
                  </p>
                  <Button
                    variant="outline"
                    onClick={() =>
                      setDraft({
                        version: policy.version,
                        paused: policy.paused,
                        enabled: policy.enabled,
                        host: policy.host_match,
                        concurrency: String(policy.maximum_concurrency),
                        interval: String(
                          policy.minimum_request_interval_seconds
                        ),
                      })
                    }
                  >
                    Reload latest policy
                  </Button>
                </div>
              )}
              <div className="flex items-center justify-between rounded-md border p-3">
                <Label htmlFor={`pause-${policy.id}`}>
                  Pause captures for matching hosts
                </Label>
                <Switch
                  id={`pause-${policy.id}`}
                  checked={draft.paused}
                  onCheckedChange={(paused) => setDraft({ ...draft, paused })}
                />
              </div>
              <DomainHostField
                disabled={fallback}
                value={draft.host}
                onChange={(host) => setDraft({ ...draft, host })}
              />
              <Field label="Maximum concurrency" htmlFor={concurrencyId}>
                <Input
                  id={concurrencyId}
                  type="number"
                  min={1}
                  value={draft.concurrency}
                  onChange={(event) =>
                    setDraft({ ...draft, concurrency: event.target.value })
                  }
                />
              </Field>
              <Field
                label="Minimum request interval (seconds)"
                htmlFor={intervalId}
              >
                <Input
                  id={intervalId}
                  type="number"
                  min={0}
                  step={0.1}
                  value={draft.interval}
                  onChange={(event) =>
                    setDraft({ ...draft, interval: event.target.value })
                  }
                />
              </Field>
              {!fallback ? (
                <div className="flex items-center justify-between rounded-md border p-3">
                  <div>
                    <p className="font-medium">Policy enabled</p>
                    <p className="text-xs text-muted-foreground">
                      Disabling this rule exposes the next matching policy. Use
                      pause to stop new captures.
                    </p>
                  </div>
                  <Switch
                    aria-label="Policy enabled"
                    checked={draft.enabled}
                    onCheckedChange={(enabled) =>
                      setDraft({ ...draft, enabled })
                    }
                  />
                </div>
              ) : null}
            </div>
          ) : null}
          <DialogFooter showCloseButton>
            <Button
              disabled={update.isPending || draft?.version !== policy.version}
              onClick={save}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function NewDomainPolicy({
  draft,
  setDraft,
}: {
  draft: Draft | null
  setDraft: (value: Draft | null) => void
}) {
  const concurrencyId = useId()
  const intervalId = useId()
  const create = useCreateDomainPolicy()

  const submit = () => {
    if (!draft) return
    const host = resolveHostInput(draft.host)
    const concurrency = Number(draft.concurrency)
    const interval = Number(draft.interval)
    if (host.error) return toast.error(host.error)
    if (
      !Number.isInteger(concurrency) ||
      concurrency < 1 ||
      concurrency > 10000
    ) {
      return toast.error(
        "Maximum concurrency must be a whole number between 1 and 10000."
      )
    }
    if (!Number.isFinite(interval) || interval < 0 || interval > 3600) {
      return toast.error("Minimum interval must be between 0 and 3600 seconds.")
    }
    create.mutate(
      {
        slug: `domain-${Date.now().toString(36)}`,
        host_match: host.host,
        maximum_concurrency: concurrency,
        minimum_request_interval_seconds: interval,
        enabled: true,
        paused: draft.paused,
      },
      { onSuccess: () => setDraft(null) }
    )
  }

  return (
    <Dialog
      open={draft !== null}
      onOpenChange={(open) => {
        if (!open) setDraft(null)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New domain policy</DialogTitle>
          <DialogDescription>
            Set live concurrency and pacing for a host or wildcard.
          </DialogDescription>
        </DialogHeader>
        {draft ? (
          <div className="grid gap-4">
            <div className="flex items-center gap-3">
              <Switch
                id="new-domain-paused"
                checked={draft.paused}
                onCheckedChange={(paused) => setDraft({ ...draft, paused })}
              />
              <Label htmlFor="new-domain-paused">
                Pause matching hosts immediately
              </Label>
            </div>
            <DomainHostField
              value={draft.host}
              onChange={(host) => setDraft({ ...draft, host })}
            />
            <Field label="Maximum concurrency" htmlFor={concurrencyId}>
              <Input
                id={concurrencyId}
                type="number"
                min={1}
                value={draft.concurrency}
                onChange={(event) =>
                  setDraft({ ...draft, concurrency: event.target.value })
                }
              />
            </Field>
            <Field
              label="Minimum request interval (seconds)"
              htmlFor={intervalId}
            >
              <Input
                id={intervalId}
                type="number"
                min={0}
                step={0.1}
                value={draft.interval}
                onChange={(event) =>
                  setDraft({ ...draft, interval: event.target.value })
                }
              />
            </Field>
          </div>
        ) : null}
        <DialogFooter showCloseButton>
          <Button disabled={create.isPending} onClick={submit}>
            Create policy
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function DomainHostField({
  value,
  disabled = false,
  onChange,
}: {
  value: string
  disabled?: boolean
  onChange: (value: string) => void
}) {
  const inputId = useId()
  const helpId = `${inputId}-help`
  const resolution = resolveHostInput(value)
  const showHelp = !disabled && Boolean(value.trim())

  return (
    <Field label="Website" htmlFor={inputId}>
      <Input
        id={inputId}
        aria-describedby={showHelp ? helpId : undefined}
        aria-invalid={showHelp && Boolean(resolution.error)}
        autoCapitalize="none"
        autoCorrect="off"
        disabled={disabled}
        inputMode="url"
        placeholder="https://books.toscrape.com/catalogue/"
        spellCheck={false}
        value={value}
        onBlur={() => {
          if (!disabled && !resolution.error) onChange(resolution.host)
        }}
        onChange={(event) => onChange(event.target.value)}
      />
      {showHelp ? (
        <p
          id={helpId}
          className={
            resolution.error
              ? "text-xs text-destructive"
              : "text-xs text-muted-foreground"
          }
        >
          {resolution.error ??
            (resolution.host.startsWith("*.")
              ? `Matches subdomains of ${resolution.host.slice(2)}.`
              : `Matches the exact host ${resolution.host}; paths, ports, and query parameters are ignored.`)}
        </p>
      ) : null}
    </Field>
  )
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor?: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

function resolveHostInput(value: string): HostResolution {
  const trimmed = value.trim().toLowerCase()
  if (!trimmed) {
    return { host: "", error: "Enter a website or hostname." }
  }
  if (trimmed === "*") {
    return { host: trimmed, error: null }
  }

  const wildcard = trimmed.startsWith("*.")
  const website = wildcard ? trimmed.slice(2) : trimmed
  if (!website || website.includes("*")) {
    return {
      host: "",
      error: "Enter a website, an exact hostname, or a *.domain wildcard.",
    }
  }

  try {
    const url = new URL(
      website.includes("://") ? website : `https://${website}`
    )
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return { host: "", error: "Enter an HTTP or HTTPS website." }
    }
    const hostname = url.hostname.toLowerCase().replace(/\.$/, "")
    if (!hostname) {
      return { host: "", error: "Enter a website with a hostname." }
    }
    return {
      host: wildcard ? `*.${hostname}` : hostname,
      error: null,
    }
  } catch {
    return { host: "", error: "Enter a valid website or hostname." }
  }
}
