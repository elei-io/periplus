import { ArrowLeftIcon, ArrowRightIcon, GaugeIcon, PlusIcon, RefreshCwIcon, SaveIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { useCreateCrawlProfile, useCrawlProfile, useCrawlProfiles, useUpdateCrawlProfile } from "@/hooks/use-resource-data"
import type { CrawlProfileRecord } from "@/types/resources"
import { ProfileSettingsForm, profileConfig, profileDraft, profileDraftError, type ProfileDraft } from "./profile-settings-form"

export function CrawlProfilesPage() {
  const query = useCrawlProfiles()
  const profiles = [...(query.data?.items ?? [])].sort((left, right) => left.cost_rank - right.cost_rank)
  const [newDraft, setNewDraft] = useState<ProfileDraft | null>(null)

  const openNew = () => {
    const draft = profileDraft()
    draft.costRank = String((profiles.at(-1)?.cost_rank ?? 0) + 10)
    setNewDraft(draft)
  }

  return <div className="flex min-h-0 w-full flex-col gap-4">
    <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
      <div className="flex items-center gap-2"><GaugeIcon className="size-4 text-muted-foreground" /><h1 className="text-lg font-medium">Profiles</h1><Badge variant="outline">{query.data?.total ?? 0}</Badge></div>
      <div className="flex gap-2"><Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCwIcon className={query.isFetching ? "animate-spin" : ""} /> Refresh</Button><Button size="sm" onClick={openNew}><PlusIcon /> New profile</Button></div>
    </section>
    {query.isLoading ? <Centered>Loading profiles…</Centered> : null}
    {!query.isLoading && profiles.length === 0 ? <Card><CardContent className="py-12 text-center"><p className="font-medium">No crawl profiles</p><p className="mt-1 text-xs text-muted-foreground">Create the first way Atlas should fetch a page.</p><Button className="mt-4" size="sm" onClick={openNew}><PlusIcon /> New profile</Button></CardContent></Card> : null}
    {profiles.length ? <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">{profiles.map((profile, index) => <ProfileCard key={profile.id} profile={profile} step={index + 1} next={profiles[index + 1]} />)}</div> : null}
    <NewProfileDialog draft={newDraft} onDraftChange={setNewDraft} onClose={() => setNewDraft(null)} />
  </div>
}

function ProfileCard({ profile, step, next }: { profile: CrawlProfileRecord; step: number; next?: CrawlProfileRecord }) {
  return <a href={`/crawl-profiles/${profile.id}`} className="group block rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
    <Card className="h-full transition-colors group-hover:bg-accent/25" size="sm">
      <CardHeader><div className="flex items-start justify-between gap-3"><div className="flex min-w-0 items-start gap-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 font-medium text-primary tabular-nums">{step}</span><div className="min-w-0"><CardTitle>{profile.name}</CardTitle><CardDescription className="mt-1 line-clamp-2">{profile.description || behaviorSummary(profile)}</CardDescription></div></div><ArrowRightIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" /></div></CardHeader>
      <CardContent><div className="flex flex-wrap gap-2"><Badge variant="outline">{transportLabel(profile)}</Badge><Badge variant="secondary">cost {profile.cost_rank}</Badge><Badge variant="outline">{profile.trial_eligible ? next ? `may trial ${next.name}` : "top of trial ladder" : "not trialled"}</Badge></div><p className="mt-3 text-xs text-muted-foreground">{behaviorSummary(profile)}</p></CardContent>
    </Card>
  </a>
}

export function CrawlProfileDetailPage({ profileId }: { profileId: string }) {
  const query = useCrawlProfile(profileId)
  const profile = query.data
  if (query.isLoading) return <Centered>Loading profile…</Centered>
  if (!profile) return <Centered>Profile not found.</Centered>
  return <div className="flex min-h-0 w-full flex-col gap-4">
    <section className="flex flex-wrap items-start justify-between gap-3 border-b pb-4"><div className="flex min-w-0 items-start gap-3"><Button variant="ghost" size="icon-sm" nativeButton={false} render={<a href="/crawl-profiles" />}><ArrowLeftIcon /></Button><div className="min-w-0"><div className="flex items-center gap-2"><GaugeIcon className="size-4 text-muted-foreground" /><h1 className="truncate text-lg font-medium">{profile.name}</h1></div><div className="mt-1 flex flex-wrap gap-2"><Badge variant="outline">{transportLabel(profile)}</Badge><Badge variant="secondary">cost {profile.cost_rank}</Badge></div></div></div><Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCwIcon className={query.isFetching ? "animate-spin" : ""} /> Refresh</Button></section>
    <ExistingProfileForm key={profile.updated_at} profile={profile} />
    <div className="border-t pt-3 text-xs text-muted-foreground">Profile key <span className="font-mono">{profile.slug}</span> · Updated {formatDate(profile.updated_at)}</div>
  </div>
}

function ExistingProfileForm({ profile }: { profile: CrawlProfileRecord }) {
  const update = useUpdateCrawlProfile(profile.id)
  const [draft, setDraft] = useState(() => profileDraft(profile))
  const save = () => {
    const error = profileDraftError(draft)
    if (error) return toast.error(error)
    update.mutate({ name: draft.name.trim(), description: draft.description.trim(), cost_rank: Number(draft.costRank), trial_eligible: draft.trialEligible, config: profileConfig(draft) })
  }
  return <><ProfileSettingsForm draft={draft} onChange={setDraft} /><div className="sticky bottom-3 z-10 flex justify-end"><Button className="shadow-lg" disabled={update.isPending} onClick={save}><SaveIcon /> {update.isPending ? "Saving…" : "Save profile"}</Button></div></>
}

function NewProfileDialog({ draft, onDraftChange, onClose }: { draft: ProfileDraft | null; onDraftChange: (draft: ProfileDraft | null) => void; onClose: () => void }) {
  const create = useCreateCrawlProfile()
  const submit = () => {
    if (!draft) return
    const error = profileDraftError(draft)
    if (error) return toast.error(error)
    create.mutate({ slug: uniqueSlug(draft.name), name: draft.name.trim(), description: draft.description.trim(), transport: draft.transport, config: profileConfig(draft), cost_rank: Number(draft.costRank), trial_eligible: draft.trialEligible }, { onSuccess: (profile) => { onClose(); window.history.pushState(null, "", `/crawl-profiles/${profile.id}`); window.dispatchEvent(new PopStateEvent("popstate")) } })
  }
  return <Dialog open={draft !== null} onOpenChange={(open) => { if (!open && !create.isPending) onClose() }}><DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-5xl"><DialogHeader><DialogTitle>New profile</DialogTitle><DialogDescription>Define a reusable way to fetch, wait for, cache, and retain a page.</DialogDescription></DialogHeader>{draft ? <ProfileSettingsForm draft={draft} onChange={(value) => onDraftChange(value)} allowTransport /> : null}<DialogFooter showCloseButton><Button disabled={!draft || create.isPending} onClick={submit}>{create.isPending ? "Creating…" : "Create profile"}</Button></DialogFooter></DialogContent></Dialog>
}

function behaviorSummary(profile: CrawlProfileRecord) {
  const config = profile.config
  if (profile.transport === "browser") {
    const mode = config.mode === "app" ? "interactive app" : config.mode === "dynamic" ? "full-page scan" : "initial page"
    const wait = config.wait === "stable" ? "after content settles" : config.wait === "network" ? "after the network is quiet" : config.wait === "fixed" ? "after 10 seconds" : "as soon as the DOM is ready"
    return `Captures the ${mode} ${wait}.`
  }
  if (profile.transport === "firecrawl") return `Uses Firecrawl with a ${Number(config.timeout_seconds ?? 90)} second timeout.`
  const artifacts = Array.isArray(config.artifact_media_types) ? config.artifact_media_types.length : 0
  return `Direct HTTP with a ${Number(config.timeout_seconds ?? 20)} second timeout${artifacts ? `; keeps ${artifacts} file type${artifacts === 1 ? "" : "s"}` : ""}.`
}
function transportLabel(profile: CrawlProfileRecord) { return profile.transport === "http" ? "Direct HTTP" : profile.transport === "browser" ? "Browser" : "Firecrawl" }
function uniqueSlug(name: string) { const base = name.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 48) || "profile"; return `${base}-${Date.now().toString(36)}` }
function Centered({ children }: { children: React.ReactNode }) { return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">{children}</div> }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) }
