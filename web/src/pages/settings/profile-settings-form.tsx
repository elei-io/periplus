/* eslint-disable react-refresh/only-export-components */
import { PlusIcon, Trash2Icon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import type { CrawlProfileRecord, CrawlTransport } from "@/types/resources"

type CacheMode = "default" | "prefer" | "refresh" | "no_store"
const defaultHttpUserAgent = "AtlasBot/0.1.0 (https://github.com/ekkuleivonen/atlas)"

export type ProfileDraft = {
  name: string
  description: string
  transport: CrawlTransport
  costRank: string
  trialEligible: boolean
  cacheMode: CacheMode
  cacheMinutes: string
  staleFallback: boolean
  staleHours: string
  blockedQualityFlags: string[]
  timeoutSeconds: string
  userAgent: string
  followRedirects: boolean
  headers: Array<{ name: string; value: string }>
  artifactMediaTypes: string[]
  artifactMaxMb: string
  browserMode: "static" | "dynamic" | "app"
  browserWait: "none" | "stable" | "network" | "fixed"
  customBrowserTiming: boolean
  finishDelaySeconds: string
  scrollDelaySeconds: string
  maxScrollSteps: string
  firecrawlApiUrl: string
  originalConfig: Record<string, unknown>
}

const qualityFlags = [
  { value: "app_shell", label: "App shell", detail: "Mostly an empty JavaScript shell" },
  { value: "lazy_load", label: "Lazy content", detail: "More content may appear after scrolling" },
  { value: "interaction_required", label: "Needs interaction", detail: "Buttons, filters, or forms may reveal content" },
]

const artifactKinds = [
  { value: "image/*", label: "Images" },
  { value: "application/pdf", label: "PDFs" },
  { value: "video/*", label: "Videos" },
]

export function profileDraft(profile?: CrawlProfileRecord): ProfileDraft {
  const config = profile?.config ?? {}
  const cache = objectValue(config.cache)
  const blockRules = objectValue(config.cache_block_rules)
  const run = objectValue(config.run_config_overrides)
  return {
    name: profile?.name ?? "",
    description: profile?.description ?? "",
    transport: profile?.transport ?? "http",
    costRank: String(profile?.cost_rank ?? 10),
    trialEligible: profile?.trial_eligible ?? true,
    cacheMode: stringChoice(cache.mode, ["prefer", "refresh", "no_store"], "default"),
    cacheMinutes: secondsAsMinutes(cache.max_age_seconds),
    staleFallback: typeof cache.stale_if_error_seconds === "number",
    staleHours: secondsAsHours(cache.stale_if_error_seconds),
    blockedQualityFlags: stringArray(blockRules.quality_flag_codes),
    timeoutSeconds: String(numberValue(config.timeout_seconds, profile?.transport === "firecrawl" ? 90 : 20)),
    userAgent: typeof config.user_agent === "string" ? config.user_agent : defaultHttpUserAgent,
    followRedirects: booleanValue(config.follow_redirects, true),
    headers: Object.entries(objectValue(config.headers)).map(([name, value]) => ({ name, value: String(value) })),
    artifactMediaTypes: stringArray(config.artifact_media_types),
    artifactMaxMb: String(Math.round(numberValue(config.artifact_max_bytes, 64 * 1024 * 1024) / 1024 / 1024)),
    browserMode: stringChoice(config.mode, ["static", "dynamic", "app"], "static"),
    browserWait: stringChoice(config.wait, ["none", "stable", "network", "fixed"], "none"),
    customBrowserTiming: ["delay_before_return_html", "scroll_delay", "max_scroll_steps"].some((key) => key in run),
    finishDelaySeconds: optionalNumber(run.delay_before_return_html),
    scrollDelaySeconds: optionalNumber(run.scroll_delay),
    maxScrollSteps: optionalNumber(run.max_scroll_steps),
    firecrawlApiUrl: typeof config.api_url === "string" ? config.api_url : "https://api.firecrawl.dev",
    originalConfig: config,
  }
}

export function profileConfig(draft: ProfileDraft): Record<string, unknown> {
  const config = { ...draft.originalConfig }
  delete config.cache
  delete config.cache_block_rules
  delete config.artifact_media_types
  delete config.artifact_max_bytes

  if (draft.cacheMode !== "default" || draft.cacheMinutes || draft.staleFallback) {
    config.cache = compact({
      mode: draft.cacheMode === "default" ? undefined : draft.cacheMode,
      max_age_seconds: draft.cacheMinutes ? Math.round(Number(draft.cacheMinutes) * 60) : undefined,
      stale_if_error_seconds: draft.staleFallback ? Math.round(Number(draft.staleHours) * 3600) : undefined,
    })
  }
  if (draft.blockedQualityFlags.length) {
    config.cache_block_rules = { quality_flag_codes: draft.blockedQualityFlags }
  }

  if (draft.transport === "http") {
    delete config.mode
    delete config.wait
    delete config.run_config_overrides
    delete config.api_url
    delete config.provider_options
    config.timeout_seconds = Number(draft.timeoutSeconds)
    config.user_agent = draft.userAgent.trim()
    config.follow_redirects = draft.followRedirects
    const headers = Object.fromEntries(
      draft.headers.filter((header) => header.name.trim()).map((header) => [header.name.trim(), header.value])
    )
    if (Object.keys(headers).length) config.headers = headers
    else delete config.headers
    if (draft.artifactMediaTypes.length) {
      config.artifact_media_types = draft.artifactMediaTypes
      config.artifact_max_bytes = Math.round(Number(draft.artifactMaxMb) * 1024 * 1024)
    }
  } else if (draft.transport === "browser") {
    delete config.timeout_seconds
    delete config.user_agent
    delete config.follow_redirects
    delete config.headers
    delete config.api_url
    delete config.provider_options
    config.mode = draft.browserMode
    config.wait = draft.browserWait
    const existingRun = objectValue(config.run_config_overrides)
    const run = { ...existingRun }
    for (const key of ["delay_before_return_html", "scroll_delay", "max_scroll_steps"]) delete run[key]
    if (draft.customBrowserTiming) {
      if (draft.finishDelaySeconds) run.delay_before_return_html = Number(draft.finishDelaySeconds)
      if (draft.scrollDelaySeconds) run.scroll_delay = Number(draft.scrollDelaySeconds)
      if (draft.maxScrollSteps) run.max_scroll_steps = Number(draft.maxScrollSteps)
    }
    config.run_config_overrides = run
  } else {
    delete config.mode
    delete config.wait
    delete config.run_config_overrides
    delete config.follow_redirects
    delete config.user_agent
    delete config.headers
    config.timeout_seconds = Number(draft.timeoutSeconds)
    config.api_url = draft.firecrawlApiUrl.trim()
  }
  return config
}

export function profileDraftError(draft: ProfileDraft): string | null {
  const cost = Number(draft.costRank)
  const timeout = Number(draft.timeoutSeconds)
  if (!draft.name.trim()) return "Give this profile a name."
  if (!Number.isInteger(cost) || cost < 0) return "Cost order must be a whole number of zero or greater."
  if (draft.transport !== "browser" && (!(timeout > 0) || (draft.transport === "firecrawl" && timeout > 300))) return "Enter a valid request timeout."
  if (draft.transport === "http" && (!draft.userAgent.trim() || !draft.userAgent.includes("(") || !draft.userAgent.includes(")"))) return "Identify the crawler and include operator contact in parentheses."
  if (draft.cacheMinutes && Number(draft.cacheMinutes) < 0) return "Cache freshness cannot be negative."
  if (draft.staleFallback && (!(Number(draft.staleHours) >= 0) || Number(draft.staleHours) * 60 < Number(draft.cacheMinutes || 0))) return "The error fallback must be at least as old as the normal cache window."
  if (draft.artifactMediaTypes.length && (!(Number(draft.artifactMaxMb) >= 1) || Number(draft.artifactMaxMb) > 256)) return "File size limit must be between 1 and 256 MB."
  if (draft.transport === "firecrawl" && !draft.firecrawlApiUrl.trim()) return "Enter the Firecrawl server URL."
  if (draft.customBrowserTiming) {
    for (const value of [draft.finishDelaySeconds, draft.scrollDelaySeconds, draft.maxScrollSteps]) {
      if (value && Number(value) < 0) return "Browser timing values cannot be negative."
    }
  }
  return null
}

export function ProfileSettingsForm({ draft, onChange, allowTransport = false }: { draft: ProfileDraft; onChange: (draft: ProfileDraft) => void; allowTransport?: boolean }) {
  const patch = (value: Partial<ProfileDraft>) => onChange({ ...draft, ...value })
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(20rem,0.65fr)]">
      <div className="grid content-start gap-4">
        <Card size="sm">
          <CardHeader><CardTitle>How pages are fetched</CardTitle><CardDescription>Choose the amount of work Atlas should do before saving a page.</CardDescription></CardHeader>
          <CardContent className="grid gap-4">
            {allowTransport ? <Field label="Fetcher"><Select value={draft.transport} onValueChange={(value) => { if (!value) return; const transport = value as CrawlTransport; patch({ transport, timeoutSeconds: transport === "firecrawl" ? "90" : draft.transport === "firecrawl" ? "20" : draft.timeoutSeconds }) }}><SelectTrigger className="w-full"><span>{transportLabel(draft.transport)}</span></SelectTrigger><SelectContent>{(["http", "browser", "firecrawl"] as const).map((value) => <SelectItem key={value} value={value}>{transportLabel(value)}</SelectItem>)}</SelectContent></Select></Field> : <div className="flex items-center justify-between rounded-md border bg-muted/20 p-3"><div><p className="font-medium">{transportLabel(draft.transport)}</p><p className="text-xs text-muted-foreground">The fetcher cannot change after a profile is created.</p></div><Badge variant="outline">{draft.transport}</Badge></div>}
            {draft.transport === "http" ? <HttpSettings draft={draft} patch={patch} /> : null}
            {draft.transport === "browser" ? <BrowserSettings draft={draft} patch={patch} /> : null}
            {draft.transport === "firecrawl" ? <FirecrawlSettings draft={draft} patch={patch} /> : null}
          </CardContent>
        </Card>
        <CacheSettings draft={draft} patch={patch} />
      </div>
      <div className="grid content-start gap-4">
        <Card size="sm">
          <CardHeader><CardTitle>Name and trial order</CardTitle></CardHeader>
          <CardContent className="grid gap-3">
            <Field label="Profile name"><Input value={draft.name} placeholder="Rendered and settled" onChange={(event) => patch({ name: event.target.value })} /></Field>
            <Field label="When to use it"><Textarea value={draft.description} placeholder="Use for sites whose content appears after JavaScript runs." onChange={(event) => patch({ description: event.target.value })} /></Field>
            <Field label="Cost order" detail="Trials move from lower to higher numbers."><Input type="number" min={0} step={1} value={draft.costRank} onChange={(event) => patch({ costRank: event.target.value })} /></Field>
            <ToggleRow label="Include in trials" detail="Atlas may test this after a cheaper profile." checked={draft.trialEligible} onCheckedChange={(value) => patch({ trialEligible: value })} />
          </CardContent>
        </Card>
        {draft.transport === "http" ? <ArtifactSettings draft={draft} patch={patch} /> : null}
      </div>
    </div>
  )
}

function HttpSettings({ draft, patch }: FormSectionProps) {
  const addHeader = () => patch({ headers: [...draft.headers, { name: "", value: "" }] })
  return <div className="grid gap-4">
    <Field label="Crawler identity" detail="A descriptive User-Agent with operator contact; do not imitate a browser."><Input value={draft.userAgent} onChange={(event) => patch({ userAgent: event.target.value })} /></Field>
    <div className="grid gap-3 sm:grid-cols-2"><Field label="Give up after" detail="Seconds"><Input type="number" min={1} step={1} value={draft.timeoutSeconds} onChange={(event) => patch({ timeoutSeconds: event.target.value })} /></Field><ToggleRow label="Follow redirects" detail="Continue when a site moves the request." checked={draft.followRedirects} onCheckedChange={(value) => patch({ followRedirects: value })} /></div>
    <div className="grid gap-2"><div className="flex items-end justify-between gap-3"><div><Label>Request headers</Label><p className="text-xs text-muted-foreground">Authentication, cookies, proxies, and User-Agent belong to explicit typed settings and cannot be added here.</p></div><Button type="button" size="sm" variant="outline" onClick={addHeader}><PlusIcon /> Add header</Button></div>{draft.headers.length ? draft.headers.map((header, index) => <div key={index} className="grid grid-cols-[minmax(7rem,0.7fr)_minmax(8rem,1fr)_auto] gap-2"><Input aria-label={`Header ${index + 1} name`} placeholder="Header name" value={header.name} onChange={(event) => patch({ headers: draft.headers.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item) })} /><Input aria-label={`Header ${index + 1} value`} placeholder="Value" value={header.value} onChange={(event) => patch({ headers: draft.headers.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item) })} /><Button type="button" size="icon-sm" variant="ghost" aria-label={`Remove header ${index + 1}`} onClick={() => patch({ headers: draft.headers.filter((_, itemIndex) => itemIndex !== index) })}><Trash2Icon /></Button></div>) : <div className="rounded-md border border-dashed p-3 text-center text-xs text-muted-foreground">No custom headers</div>}</div>
  </div>
}

function BrowserSettings({ draft, patch }: FormSectionProps) {
  return <div className="grid gap-4"><ChoiceGrid label="Page depth" value={draft.browserMode} options={[{ value: "static", label: "Essential", detail: "Load the initial page without scrolling." }, { value: "dynamic", label: "Full page", detail: "Scroll through lazy-loaded content." }, { value: "app", label: "Interactive app", detail: "Hydrate the app, then scan the full page." }]} onChange={(value) => patch({ browserMode: value as ProfileDraft["browserMode"] })} /><ChoiceGrid label="Ready when" value={draft.browserWait} options={[{ value: "none", label: "DOM ready", detail: "Fastest; use the first usable DOM." }, { value: "stable", label: "Content settles", detail: "Wait for links and mutations to become quiet." }, { value: "network", label: "Network quiet", detail: "Wait until background requests stop." }, { value: "fixed", label: "10 seconds", detail: "Always allow a fixed hydration window." }]} onChange={(value) => patch({ browserWait: value as ProfileDraft["browserWait"] })} /><ToggleRow label="Tune browser timing" detail="Override the safe defaults for this page depth." checked={draft.customBrowserTiming} onCheckedChange={(value) => patch({ customBrowserTiming: value })} />{draft.customBrowserTiming ? <div className="grid gap-3 rounded-md border bg-muted/15 p-3 sm:grid-cols-3"><Field label="Final pause" detail="Seconds"><Input type="number" min={0} step={0.1} placeholder="Automatic" value={draft.finishDelaySeconds} onChange={(event) => patch({ finishDelaySeconds: event.target.value })} /></Field><Field label="Between scrolls" detail="Seconds"><Input type="number" min={0} step={0.1} placeholder="Automatic" value={draft.scrollDelaySeconds} onChange={(event) => patch({ scrollDelaySeconds: event.target.value })} /></Field><Field label="Scroll limit" detail="Steps"><Input type="number" min={0} step={1} placeholder="Automatic" value={draft.maxScrollSteps} onChange={(event) => patch({ maxScrollSteps: event.target.value })} /></Field></div> : null}</div>
}

function FirecrawlSettings({ draft, patch }: FormSectionProps) {
  return <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]"><Field label="Firecrawl server"><Input type="url" value={draft.firecrawlApiUrl} onChange={(event) => patch({ firecrawlApiUrl: event.target.value })} /></Field><Field label="Give up after" detail="Seconds, maximum 300"><Input type="number" min={1} max={300} value={draft.timeoutSeconds} onChange={(event) => patch({ timeoutSeconds: event.target.value })} /></Field></div>
}

function CacheSettings({ draft, patch }: FormSectionProps) {
  return <Card size="sm"><CardHeader><CardTitle>Reuse recent pages</CardTitle><CardDescription>Control freshness without fetching the same URL unnecessarily.</CardDescription></CardHeader><CardContent className="grid gap-4"><div className="grid gap-3 sm:grid-cols-2"><Field label="On a repeat visit"><Select value={draft.cacheMode} onValueChange={(value) => value && patch({ cacheMode: value as CacheMode })}><SelectTrigger className="w-full"><span>{cacheModeLabel(draft.cacheMode)}</span></SelectTrigger><SelectContent><SelectItem value="default">Use the Atlas default</SelectItem><SelectItem value="prefer">Reuse a recent copy</SelectItem><SelectItem value="refresh">Always fetch a fresh copy</SelectItem><SelectItem value="no_store">Fetch without reusable cache</SelectItem></SelectContent></Select></Field><Field label="Consider a copy recent for" detail="Minutes; blank uses the Atlas default"><Input type="number" min={0} step={1} placeholder="Automatic" value={draft.cacheMinutes} onChange={(event) => patch({ cacheMinutes: event.target.value })} /></Field></div><ToggleRow label="Fall back to an older copy after an error" detail="Useful when a website is temporarily unavailable." checked={draft.staleFallback} onCheckedChange={(value) => patch({ staleFallback: value })} />{draft.staleFallback ? <Field label="Oldest acceptable fallback" detail="Hours"><Input className="max-w-40" type="number" min={0} step={1} value={draft.staleHours} onChange={(event) => patch({ staleHours: event.target.value })} /></Field> : null}<div className="grid gap-2 border-t pt-4"><div><Label>Do not reuse pages that look incomplete</Label><p className="text-xs text-muted-foreground">A matching quality signal forces a fresh acquisition next time.</p></div><div className="grid gap-2 sm:grid-cols-3">{qualityFlags.map((flag) => <ToggleCard key={flag.value} label={flag.label} detail={flag.detail} checked={draft.blockedQualityFlags.includes(flag.value)} onCheckedChange={(checked) => patch({ blockedQualityFlags: toggleValue(draft.blockedQualityFlags, flag.value, checked) })} />)}</div></div></CardContent></Card>
}

function ArtifactSettings({ draft, patch }: FormSectionProps) {
  return <Card size="sm"><CardHeader><CardTitle>Files to keep</CardTitle><CardDescription>Store selected responses as immutable artifacts instead of discarding them as non-HTML.</CardDescription></CardHeader><CardContent className="grid gap-3"><div className="grid gap-2 sm:grid-cols-3">{artifactKinds.map((kind) => <ToggleCard key={kind.value} label={kind.label} checked={draft.artifactMediaTypes.includes(kind.value)} onCheckedChange={(checked) => patch({ artifactMediaTypes: toggleValue(draft.artifactMediaTypes, kind.value, checked) })} />)}</div>{draft.artifactMediaTypes.length ? <Field label="Largest file to keep" detail="MB, maximum 256"><Input className="max-w-40" type="number" min={1} max={256} step={1} value={draft.artifactMaxMb} onChange={(event) => patch({ artifactMaxMb: event.target.value })} /></Field> : null}</CardContent></Card>
}

type FormSectionProps = { draft: ProfileDraft; patch: (value: Partial<ProfileDraft>) => void }

function Field({ label, detail, children }: { label: string; detail?: string; children: React.ReactNode }) { return <div className="grid content-start gap-1"><Label>{label}</Label>{children}{detail ? <p className="text-xs text-muted-foreground">{detail}</p> : null}</div> }
function ToggleRow({ label, detail, checked, onCheckedChange }: { label: string; detail?: string; checked: boolean; onCheckedChange: (checked: boolean) => void }) { return <div className="flex min-h-12 items-center justify-between gap-4 rounded-md border bg-muted/10 px-3 py-2"><div><p className="font-medium">{label}</p>{detail ? <p className="text-xs text-muted-foreground">{detail}</p> : null}</div><Switch checked={checked} onCheckedChange={onCheckedChange} /></div> }
function ToggleCard({ label, detail, checked, onCheckedChange }: { label: string; detail?: string; checked: boolean; onCheckedChange: (checked: boolean) => void }) { return <div className={`rounded-md border p-3 text-left transition-colors ${checked ? "border-primary/60 bg-primary/8 ring-1 ring-primary/20" : "bg-muted/10"}`}><div className="flex items-center justify-between gap-2"><span className="font-medium">{label}</span><Switch checked={checked} aria-label={label} onCheckedChange={onCheckedChange} /></div>{detail ? <p className="mt-1 text-xs text-muted-foreground">{detail}</p> : null}</div> }
function ChoiceGrid({ label, value, options, onChange }: { label: string; value: string; options: Array<{ value: string; label: string; detail: string }>; onChange: (value: string) => void }) { return <div className="grid gap-2"><Label>{label}</Label><div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">{options.map((option) => <button key={option.value} type="button" aria-pressed={value === option.value} onClick={() => onChange(option.value)} className={`rounded-md border p-3 text-left transition-colors ${value === option.value ? "border-primary/60 bg-primary/8 ring-1 ring-primary/20" : "bg-muted/10 hover:bg-muted/25"}`}><p className="font-medium">{option.label}</p><p className="mt-1 text-xs text-muted-foreground">{option.detail}</p></button>)}</div></div> }

function transportLabel(value: CrawlTransport) { return value === "http" ? "Direct HTTP" : value === "browser" ? "Browser" : "Firecrawl" }
function cacheModeLabel(value: CacheMode) { return value === "prefer" ? "Reuse a recent copy" : value === "refresh" ? "Always fetch a fresh copy" : value === "no_store" ? "Fetch without reusable cache" : "Use the Atlas default" }
function toggleValue(values: string[], value: string, checked: boolean) { return checked ? Array.from(new Set([...values, value])) : values.filter((item) => item !== value) }
function objectValue(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {} }
function stringArray(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [] }
function numberValue(value: unknown, fallback: number) { return typeof value === "number" ? value : fallback }
function booleanValue(value: unknown, fallback: boolean) { return typeof value === "boolean" ? value : fallback }
function optionalNumber(value: unknown) { return typeof value === "number" ? String(value) : "" }
function secondsAsMinutes(value: unknown) { return typeof value === "number" ? String(value / 60) : "" }
function secondsAsHours(value: unknown) { return typeof value === "number" ? String(value / 3600) : "24" }
function stringChoice<T extends string>(value: unknown, options: readonly T[], fallback: T): T { return typeof value === "string" && options.includes(value as T) ? value as T : fallback }
function compact(value: Record<string, unknown>) { return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined)) }
