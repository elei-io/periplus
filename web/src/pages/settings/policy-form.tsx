/* eslint-disable react-refresh/only-export-components */
import { Globe2Icon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import type { CrawlPolicyRecord, CrawlProfileRecord } from "@/types/resources"

export type PolicyScope = "site" | "section" | "page"
export type PolicyDraft = {
  website: string
  scheme: "*" | "http" | "https"
  scope: PolicyScope
  path: string
  profileId: string
  maxConcurrency: string
  enabled: boolean
}

export function policyDraft(policy?: CrawlPolicyRecord, profiles: CrawlProfileRecord[] = []): PolicyDraft {
  return {
    website: policy?.host === "*" ? "" : policy?.host ?? "",
    scheme: policy?.scheme ?? "*",
    scope: policy ? policy.path_prefix === "/" && policy.path_mode === "prefix" ? "site" : policy.path_mode === "exact" ? "page" : "section" : "site",
    path: policy?.path_prefix ?? "/",
    profileId: policy?.profile.id ?? profiles[0]?.id ?? "",
    maxConcurrency: String(policy?.max_concurrency ?? 4),
    enabled: policy?.enabled ?? true,
  }
}

export function policyValues(draft: PolicyDraft, defaultPolicy = false) {
  const parsed = parseWebsite(draft.website)
  return {
    scheme: defaultPolicy ? "*" as const : draft.scheme,
    host: defaultPolicy ? "*" : parsed.host,
    path_prefix: defaultPolicy || draft.scope === "site" ? "/" : normalizedPath(draft.path),
    path_mode: defaultPolicy || draft.scope !== "page" ? "prefix" as const : "exact" as const,
    profile_id: draft.profileId,
    max_concurrency: Number(draft.maxConcurrency),
    enabled: defaultPolicy ? true : draft.enabled,
  }
}

export function policyDraftError(draft: PolicyDraft, defaultPolicy = false): string | null {
  const parsed = parseWebsite(draft.website)
  if (!defaultPolicy && !parsed.host) return "Enter the website this policy applies to."
  if (!defaultPolicy && (parsed.host.includes("/") || parsed.host.includes(" "))) return "Enter a hostname such as example.com, or paste a complete URL."
  if (draft.scope !== "site" && !normalizedPath(draft.path).startsWith("/")) return "The page path must start with /."
  if (!draft.profileId) return "Choose how Atlas should fetch matching pages."
  if (!Number.isInteger(Number(draft.maxConcurrency)) || Number(draft.maxConcurrency) < 1) return "Simultaneous requests must be a whole number of at least one."
  return null
}

export function PolicyForm({ draft, onChange, profiles, defaultPolicy = false }: { draft: PolicyDraft; onChange: (draft: PolicyDraft) => void; profiles: CrawlProfileRecord[]; defaultPolicy?: boolean }) {
  const patch = (value: Partial<PolicyDraft>) => onChange({ ...draft, ...value })
  const chosen = profiles.find((profile) => profile.id === draft.profileId)
  return <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(20rem,0.55fr)]">
    <Card size="sm"><CardHeader><CardTitle>Where this policy applies</CardTitle><CardDescription>More specific website and path rules take precedence automatically.</CardDescription></CardHeader><CardContent className="grid gap-4">
      {defaultPolicy ? <div className="flex items-center gap-3 rounded-md border bg-muted/20 p-3"><Globe2Icon className="size-5 text-muted-foreground" /><div><p className="font-medium">Every website and page</p><p className="text-xs text-muted-foreground">This fallback rule keeps Atlas usable when no more specific policy exists.</p></div></div> : <>
        <Field label="Website" detail="Enter a hostname such as example.com, or paste a URL."><Input autoComplete="off" placeholder="example.com" value={draft.website} onChange={(event) => patch({ website: event.target.value })} /></Field>
        <ChoiceGrid label="Connection" value={draft.scheme} options={[{ value: "*", label: "HTTP and HTTPS", detail: "Recommended for most sites" }, { value: "https", label: "HTTPS only", detail: "Secure URLs only" }, { value: "http", label: "HTTP only", detail: "Unencrypted URLs only" }]} onChange={(value) => patch({ scheme: value as PolicyDraft["scheme"] })} />
        <ChoiceGrid label="Pages" value={draft.scope} options={[{ value: "site", label: "Entire website", detail: "Every path on this host" }, { value: "section", label: "One section", detail: "A path and everything below it" }, { value: "page", label: "One exact page", detail: "Only a single path" }]} onChange={(value) => patch({ scope: value as PolicyScope })} />
        {draft.scope !== "site" ? <Field label={draft.scope === "page" ? "Page path" : "Section path"} detail={draft.scope === "section" ? "Everything below this path is included." : "Query parameters are not part of policy matching."}><Input placeholder="/docs/" value={draft.path} onChange={(event) => patch({ path: event.target.value })} /></Field> : null}
      </>}
      <div className="rounded-md border border-dashed bg-muted/10 px-3 py-2"><p className="text-[0.625rem] uppercase text-muted-foreground">Matches</p><p className="mt-0.5 font-medium">{matchSummary(draft, defaultPolicy)}</p></div>
    </CardContent></Card>
    <div className="grid content-start gap-4"><Card size="sm"><CardHeader><CardTitle>How Atlas should crawl</CardTitle></CardHeader><CardContent className="grid gap-4">
      <Field label="Profile"><Select value={draft.profileId} onValueChange={(value) => value && patch({ profileId: value })}><SelectTrigger className="w-full"><span>{chosen?.name ?? "Choose a profile"}</span></SelectTrigger><SelectContent>{profiles.map((profile) => <SelectItem key={profile.id} value={profile.id}>{profile.name} · {transportLabel(profile)}</SelectItem>)}</SelectContent></Select>{chosen ? <div className="rounded-md bg-muted/20 p-3"><div className="flex flex-wrap gap-2"><Badge variant="outline">{transportLabel(chosen)}</Badge><Badge variant="secondary">cost {chosen.cost_rank}</Badge></div><p className="mt-2 text-xs text-muted-foreground">{chosen.description}</p></div> : null}</Field>
      <Field label="Simultaneous requests per website" detail="A polite limit shared by all workers crawling the same domain."><Input type="number" min={1} step={1} value={draft.maxConcurrency} onChange={(event) => patch({ maxConcurrency: event.target.value })} /></Field>
      {!defaultPolicy ? <div className="flex min-h-12 items-center justify-between gap-4 rounded-md border bg-muted/10 px-3 py-2"><div><p className="font-medium">Policy enabled</p><p className="text-xs text-muted-foreground">Disabled policies stay saved but do not match URLs.</p></div><Switch checked={draft.enabled} onCheckedChange={(value) => patch({ enabled: value })} /></div> : null}
    </CardContent></Card></div>
  </div>
}

function Field({ label, detail, children }: { label: string; detail?: string; children: React.ReactNode }) { return <div className="grid content-start gap-1"><Label>{label}</Label>{children}{detail ? <p className="text-xs text-muted-foreground">{detail}</p> : null}</div> }
function ChoiceGrid({ label, value, options, onChange }: { label: string; value: string; options: Array<{ value: string; label: string; detail: string }>; onChange: (value: string) => void }) { return <div className="grid gap-2"><Label>{label}</Label><div className="grid gap-2 sm:grid-cols-3">{options.map((option) => <button key={option.value} type="button" aria-pressed={value === option.value} onClick={() => onChange(option.value)} className={`rounded-md border p-3 text-left transition-colors ${value === option.value ? "border-primary/60 bg-primary/8 ring-1 ring-primary/20" : "bg-muted/10 hover:bg-muted/25"}`}><p className="font-medium">{option.label}</p><p className="mt-1 text-xs text-muted-foreground">{option.detail}</p></button>)}</div></div> }
function parseWebsite(value: string): { host: string } { const trimmed = value.trim().toLowerCase(); if (!trimmed) return { host: "" }; try { const url = new URL(trimmed.includes("://") ? trimmed : `https://${trimmed}`); return { host: url.host } } catch { return { host: trimmed } } }
function normalizedPath(value: string) { const trimmed = value.trim(); return trimmed ? trimmed.startsWith("/") ? trimmed : `/${trimmed}` : "/" }
function matchSummary(draft: PolicyDraft, defaultPolicy: boolean) { if (defaultPolicy) return "All pages on all websites"; const host = parseWebsite(draft.website).host || "this website"; if (draft.scope === "site") return `All pages on ${host}`; const path = normalizedPath(draft.path); return draft.scope === "page" ? `${host}${path} only` : `${host}${path} and everything below it` }
function transportLabel(profile: CrawlProfileRecord) { return profile.transport === "http" ? "Direct HTTP" : profile.transport === "browser" ? "Browser" : "Firecrawl" }
