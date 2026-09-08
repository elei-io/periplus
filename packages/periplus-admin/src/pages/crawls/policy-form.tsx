/* eslint-disable react-refresh/only-export-components */
import { Globe2Icon } from "lucide-react"

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import type { ContentPolicyRecord, ResponseOutcome } from "@/types/resources"

import {
  contentTypeGroups,
  contentTypeGroupState,
  setContentTypeGroup,
} from "./content-types"

export type PolicyScope = "site" | "section" | "page"
export type PolicyHostScope = "exact" | "subdomains"
export type PolicyDraft = {
  website: string
  hostScope: PolicyHostScope
  scheme: "*" | "http" | "https"
  scope: PolicyScope
  path: string
  acceptedContentTypes: string
  navigationTimeoutMs: number
  contextReplacementRetries: number
  contextReplacementSettleMs: number
  waitDynamic: boolean
  waitDynamicMaximumMs: number
  waitDynamicSampleMs: number
  waitDynamicStableSamples: number
  waitFixed: boolean
  waitFixedDurationMs: number
  scroll: boolean
  scrollMaximumIterations: number
  scrollViewportRatio: number
  scrollWaitMs: number
  scrollStableBottomSamples: number
  expand: boolean
  expandMaximumActions: number
  expandWaitMs: number
  tooManyRequests: ResponseOutcome
  clientError: ResponseOutcome
  serverError: ResponseOutcome
  unsupportedContentType: ResponseOutcome
  enabled: boolean
}

export function policyDraft(policy?: ContentPolicyRecord): PolicyDraft {
  const rules = policy?.content.response_rules.http_status ?? []
  const outcome = (
    minimum: number,
    maximum: number,
    fallback: ResponseOutcome
  ) =>
    rules.find((rule) => rule.minimum === minimum && rule.maximum === maximum)
      ?.outcome ?? fallback
  return {
    website: policy?.host.startsWith("*.")
      ? policy.host.slice(2)
      : (policy?.host ?? ""),
    hostScope: policy?.host.startsWith("*.") ? "subdomains" : "exact",
    scheme: policy?.scheme ?? "*",
    scope: policy
      ? policy.path_prefix === "/" && policy.path_mode === "prefix"
        ? "site"
        : policy.path_mode === "exact"
          ? "page"
          : "section"
      : "site",
    path: policy?.path_prefix ?? "/",
    acceptedContentTypes: (
      policy?.content.accepted_content_types ?? [
        "text/html",
        "application/xhtml+xml",
      ]
    ).join(", "),
    navigationTimeoutMs:
      policy?.content.completion.navigation.timeout_ms ?? 30000,
    contextReplacementRetries:
      policy?.content.completion.navigation.context_replacement_retries ?? 2,
    contextReplacementSettleMs:
      policy?.content.completion.navigation.context_replacement_settle_ms ??
      1000,
    waitDynamic: policy?.content.completion.wait_dynamic.enabled ?? true,
    waitDynamicMaximumMs:
      policy?.content.completion.wait_dynamic.maximum_wait_ms ?? 8000,
    waitDynamicSampleMs:
      policy?.content.completion.wait_dynamic.sample_interval_ms ?? 250,
    waitDynamicStableSamples:
      policy?.content.completion.wait_dynamic.stable_samples ?? 3,
    waitFixed: policy?.content.completion.wait_fixed.enabled ?? false,
    waitFixedDurationMs: policy?.content.completion.wait_fixed.duration_ms ?? 0,
    scroll: policy?.content.completion.scroll.enabled ?? true,
    scrollMaximumIterations:
      policy?.content.completion.scroll.maximum_iterations ?? 30,
    scrollViewportRatio:
      policy?.content.completion.scroll.viewport_ratio ?? 0.85,
    scrollWaitMs: policy?.content.completion.scroll.wait_ms ?? 250,
    scrollStableBottomSamples:
      policy?.content.completion.scroll.stable_bottom_samples ?? 3,
    expand: policy?.content.completion.expand.enabled ?? true,
    expandMaximumActions:
      policy?.content.completion.expand.maximum_actions ?? 10,
    expandWaitMs: policy?.content.completion.expand.wait_ms ?? 500,
    tooManyRequests: outcome(429, 429, "retry"),
    clientError: outcome(400, 499, "fail"),
    serverError: outcome(500, 599, "retry"),
    unsupportedContentType:
      policy?.content.response_rules.unsupported_content_type ?? "skip",
    enabled: policy?.enabled ?? true,
  }
}

export function policyValues(draft: PolicyDraft, defaultPolicy = false) {
  const websiteHost = parseWebsite(draft.website).host
  return {
    scheme: defaultPolicy ? ("*" as const) : draft.scheme,
    host: defaultPolicy
      ? "*"
      : draft.hostScope === "subdomains"
        ? `*.${websiteHost}`
        : websiteHost,
    path_prefix:
      defaultPolicy || draft.scope === "site"
        ? "/"
        : normalizedPath(draft.path),
    path_mode:
      defaultPolicy || draft.scope !== "page"
        ? ("prefix" as const)
        : ("exact" as const),
    content: {
      accepted_content_types: draft.acceptedContentTypes
        .split(",")
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean),
      response_rules: {
        http_status: [
          { minimum: 408, maximum: 408, outcome: "retry" as const },
          { minimum: 425, maximum: 425, outcome: "retry" as const },
          { minimum: 429, maximum: 429, outcome: draft.tooManyRequests },
          { minimum: 500, maximum: 599, outcome: draft.serverError },
          { minimum: 400, maximum: 499, outcome: draft.clientError },
        ],
        unsupported_content_type: draft.unsupportedContentType,
      },
      completion: {
        navigation: {
          timeout_ms: draft.navigationTimeoutMs,
          context_replacement_retries: draft.contextReplacementRetries,
          context_replacement_settle_ms: draft.contextReplacementSettleMs,
        },
        wait_dynamic: {
          enabled: draft.waitDynamic,
          maximum_wait_ms: draft.waitDynamicMaximumMs,
          sample_interval_ms: draft.waitDynamicSampleMs,
          stable_samples: draft.waitDynamicStableSamples,
        },
        wait_fixed: {
          enabled: draft.waitFixed,
          duration_ms: draft.waitFixedDurationMs,
        },
        scroll: {
          enabled: draft.scroll,
          maximum_iterations: draft.scrollMaximumIterations,
          viewport_ratio: draft.scrollViewportRatio,
          wait_ms: draft.scrollWaitMs,
          stable_bottom_samples: draft.scrollStableBottomSamples,
        },
        expand: {
          enabled: draft.expand,
          maximum_actions: draft.expandMaximumActions,
          wait_ms: draft.expandWaitMs,
        },
      },
    },
    enabled: defaultPolicy ? true : draft.enabled,
  }
}

export function policyDraftError(draft: PolicyDraft, defaultPolicy = false) {
  const host = parseWebsite(draft.website).host
  if (!defaultPolicy && (!host || host.includes("/") || host.includes(" ")))
    return "Enter a valid website hostname or * for every website."
  if (!defaultPolicy && draft.hostScope === "subdomains" && host.includes("*"))
    return "Enter a root hostname before selecting all subdomains."
  if (!draft.acceptedContentTypes.split(",").some((value) => value.trim()))
    return "Accept at least one content type."
  if (draft.waitFixed && draft.waitFixedDurationMs <= 0)
    return "Fixed wait must be greater than zero when enabled."
  return null
}

export function PolicyForm({
  draft,
  onChange,
  defaultPolicy = false,
}: {
  draft: PolicyDraft
  onChange: (draft: PolicyDraft) => void
  defaultPolicy?: boolean
}) {
  const patch = (value: Partial<PolicyDraft>) =>
    onChange({ ...draft, ...value })
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card size="sm">
        <CardHeader>
          <CardTitle>Where this policy applies</CardTitle>
          <CardDescription>
            The most specific matching capture policy is frozen into each crawl.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {defaultPolicy ? (
            <div className="flex items-center gap-3 rounded-md border p-3">
              <Globe2Icon className="size-5 text-muted-foreground" />
              <div>
                <p className="font-medium">Every website and page</p>
                <p className="text-xs text-muted-foreground">
                  Required base matcher · *://*/*
                </p>
              </div>
            </div>
          ) : (
            <>
              <Field label="Website">
                <Input
                  placeholder="wikipedia.org or *"
                  value={draft.website}
                  onChange={(event) => patch({ website: event.target.value })}
                />
                <p className="text-xs text-muted-foreground">
                  {draft.hostScope === "subdomains"
                    ? `Matches subdomains such as en.${parseWebsite(draft.website).host || "wikipedia.org"}; the root hostname is separate.`
                    : "Matches this exact hostname."}
                </p>
              </Field>
              <Choice
                label="Hosts"
                value={draft.hostScope}
                options={[
                  ["exact", "This hostname"],
                  ["subdomains", "All subdomains"],
                ]}
                onChange={(value) =>
                  patch({ hostScope: value as PolicyHostScope })
                }
              />
              <Choice
                label="Connection"
                value={draft.scheme}
                options={[
                  ["*", "HTTP and HTTPS"],
                  ["https", "HTTPS only"],
                  ["http", "HTTP only"],
                ]}
                onChange={(value) =>
                  patch({ scheme: value as PolicyDraft["scheme"] })
                }
              />
              <Choice
                label="Pages"
                value={draft.scope}
                options={[
                  ["site", "Entire website"],
                  ["section", "One section"],
                  ["page", "One exact page"],
                ]}
                onChange={(value) => patch({ scope: value as PolicyScope })}
              />
              {draft.scope !== "site" ? (
                <Field label="Path">
                  <Input
                    value={draft.path}
                    onChange={(event) => patch({ path: event.target.value })}
                  />
                </Field>
              ) : null}
              <Toggle
                label="Policy enabled"
                description="Disabled policies do not match URLs."
                checked={draft.enabled}
                onCheckedChange={(enabled) => patch({ enabled })}
              />
            </>
          )}
        </CardContent>
      </Card>
      <div className="grid gap-4">
        <Card size="sm">
          <CardHeader>
            <CardTitle>Content completion</CardTitle>
            <CardDescription>
              Disable every method to make the crawl eligible for a static HTTP
              capture.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            <div className="grid gap-3 rounded-md border p-3">
              <div>
                <p className="font-medium">Navigation</p>
                <p className="text-xs text-muted-foreground">
                  Bound initial loading and recovery when the page replaces its
                  document.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-3">
                <NumberField
                  label="Timeout (ms)"
                  value={draft.navigationTimeoutMs}
                  onChange={(value) => patch({ navigationTimeoutMs: value })}
                />
                <NumberField
                  label="Context replacement retries"
                  value={draft.contextReplacementRetries}
                  onChange={(value) =>
                    patch({ contextReplacementRetries: value })
                  }
                />
                <NumberField
                  label="Settle after replacement (ms)"
                  value={draft.contextReplacementSettleMs}
                  onChange={(value) =>
                    patch({ contextReplacementSettleMs: value })
                  }
                />
              </div>
            </div>
            <Method
              label="Dynamic wait"
              description="Wait only until rendered content becomes stable."
              checked={draft.waitDynamic}
              onCheckedChange={(value) => patch({ waitDynamic: value })}
            >
              <NumberField
                label="Maximum wait (ms)"
                value={draft.waitDynamicMaximumMs}
                onChange={(value) => patch({ waitDynamicMaximumMs: value })}
              />
              <NumberField
                label="Sample interval (ms)"
                value={draft.waitDynamicSampleMs}
                onChange={(value) => patch({ waitDynamicSampleMs: value })}
              />
              <NumberField
                label="Stable samples"
                value={draft.waitDynamicStableSamples}
                onChange={(value) => patch({ waitDynamicStableSamples: value })}
              />
            </Method>
            <Method
              label="Fixed wait"
              description="Always wait after dynamic settling as a break-glass fallback."
              checked={draft.waitFixed}
              onCheckedChange={(value) => patch({ waitFixed: value })}
            >
              <NumberField
                label="Duration (ms)"
                value={draft.waitFixedDurationMs}
                onChange={(value) => patch({ waitFixedDurationMs: value })}
              />
            </Method>
            <Method
              label="Scroll"
              description="Scroll until the bottom remains stable."
              checked={draft.scroll}
              onCheckedChange={(value) => patch({ scroll: value })}
            >
              <NumberField
                label="Maximum iterations"
                value={draft.scrollMaximumIterations}
                onChange={(value) => patch({ scrollMaximumIterations: value })}
              />
              <NumberField
                label="Viewport ratio"
                value={draft.scrollViewportRatio}
                step={0.05}
                onChange={(value) => patch({ scrollViewportRatio: value })}
              />
              <NumberField
                label="Wait per scroll (ms)"
                value={draft.scrollWaitMs}
                onChange={(value) => patch({ scrollWaitMs: value })}
              />
              <NumberField
                label="Stable bottom samples"
                value={draft.scrollStableBottomSamples}
                onChange={(value) =>
                  patch({ scrollStableBottomSamples: value })
                }
              />
            </Method>
            <Method
              label="Expand"
              description="Activate conservative visible load-more controls."
              checked={draft.expand}
              onCheckedChange={(value) => patch({ expand: value })}
            >
              <NumberField
                label="Maximum actions"
                value={draft.expandMaximumActions}
                onChange={(value) => patch({ expandMaximumActions: value })}
              />
              <NumberField
                label="Wait per action (ms)"
                value={draft.expandWaitMs}
                onChange={(value) => patch({ expandWaitMs: value })}
              />
            </Method>
          </CardContent>
        </Card>
        <Card size="sm">
          <CardHeader>
            <CardTitle>Content to keep</CardTitle>
            <CardDescription>
              Web pages are stored as crawl documents. Enabled downloads are
              retained as exact raw artifacts.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2">
              {contentTypeGroups.map((group) => {
                const state = contentTypeGroupState(
                  draft.acceptedContentTypes,
                  group.id
                )
                return (
                  <Toggle
                    key={group.id}
                    label={group.label}
                    description={`${group.description}${state === "partial" ? " Some formats are currently enabled." : ""}`}
                    checked={state !== "off"}
                    onCheckedChange={(enabled) =>
                      patch({
                        acceptedContentTypes: setContentTypeGroup(
                          draft.acceptedContentTypes,
                          group.id,
                          enabled
                        ),
                      })
                    }
                  />
                )
              })}
            </div>
            <Field label="Advanced MIME types">
              <Input
                placeholder="application/x-custom, image/svg+xml"
                value={draft.acceptedContentTypes}
                onChange={(event) =>
                  patch({ acceptedContentTypes: event.target.value })
                }
              />
              <p className="text-xs text-muted-foreground">
                Comma separated. This is the exact saved policy; use it for
                formats not covered by the switches.
              </p>
            </Field>
            <div>
              <p className="font-medium">Response handling</p>
              <p className="text-xs text-muted-foreground">
                Choose how HTTP errors and disabled content kinds settle.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Outcome
                label="HTTP 429"
                value={draft.tooManyRequests}
                onChange={(value) => patch({ tooManyRequests: value })}
              />
              <Outcome
                label="Other HTTP 4xx"
                value={draft.clientError}
                onChange={(value) => patch({ clientError: value })}
              />
              <Outcome
                label="HTTP 5xx"
                value={draft.serverError}
                onChange={(value) => patch({ serverError: value })}
              />
              <Outcome
                label="Unsupported content type"
                value={draft.unsupportedContentType}
                onChange={(value) => patch({ unsupportedContentType: value })}
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function Toggle({
  label,
  description,
  checked,
  onCheckedChange,
}: {
  label: string
  description: string
  checked: boolean
  onCheckedChange: (value: boolean) => void
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border p-3">
      <div>
        <p className="font-medium">{label}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} />
    </div>
  )
}
function Method({
  label,
  description,
  checked,
  disabled = false,
  onCheckedChange,
  children,
}: {
  label: string
  description: string
  checked: boolean
  disabled?: boolean
  onCheckedChange: (value: boolean) => void
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-3 rounded-md border p-3">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="font-medium">{label}</p>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <Switch
          checked={checked}
          disabled={disabled}
          onCheckedChange={onCheckedChange}
        />
      </div>
      {checked ? (
        <div className="grid gap-3 sm:grid-cols-2">{children}</div>
      ) : null}
    </div>
  )
}
function NumberField({
  label,
  value,
  step = 1,
  disabled = false,
  onChange,
}: {
  label: string
  value: number
  step?: number
  disabled?: boolean
  onChange: (value: number) => void
}) {
  return (
    <Field label={label}>
      <Input
        type="number"
        min={0}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </Field>
  )
}
function Outcome({
  label,
  value,
  disabled = false,
  onChange,
}: {
  label: string
  value: ResponseOutcome
  disabled?: boolean
  onChange: (value: ResponseOutcome) => void
}) {
  return (
    <Field label={label}>
      <Select
        value={value}
        disabled={disabled}
        onValueChange={(next) => next && onChange(next as ResponseOutcome)}
      >
        <SelectTrigger>
          <span className="capitalize">{value}</span>
        </SelectTrigger>
        <SelectContent>
          {["retry", "fail", "skip", "accept"].map((outcome) => (
            <SelectItem key={outcome} value={outcome}>
              {outcome}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  )
}
function Field({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1">
      <Label>{label}</Label>
      {children}
    </div>
  )
}
function Choice({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: string[][]
  onChange: (value: string) => void
}) {
  return (
    <div className="grid gap-2">
      <Label>{label}</Label>
      <div
        className={`grid gap-2 ${options.length === 2 ? "grid-cols-2" : "grid-cols-3"}`}
      >
        {options.map(([key, name]) => (
          <Button
            key={key}
            type="button"
            variant="outline"
            aria-pressed={value === key}
            onClick={() => onChange(key)}
            className="h-auto justify-start p-3 text-left aria-pressed:border-primary aria-pressed:bg-primary/8"
          >
            {name}
          </Button>
        ))}
      </div>
    </div>
  )
}
function parseWebsite(value: string) {
  const trimmed = value.trim().toLowerCase()
  if (!trimmed) return { host: "" }
  try {
    return {
      host: new URL(trimmed.includes("://") ? trimmed : `https://${trimmed}`)
        .host,
    }
  } catch {
    return { host: trimmed }
  }
}
function normalizedPath(value: string) {
  const trimmed = value.trim()
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`
}
