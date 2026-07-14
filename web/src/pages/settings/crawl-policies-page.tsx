import { CheckCircle2Icon, FilterIcon, PlusIcon, RefreshCwIcon, ShieldCheckIcon, XCircleIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { ResourcePagination, RESOURCE_PAGE_SIZE } from "@/components/resources/resource-pagination"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useCreateCrawlPolicy, useCrawlPolicies, useCrawlProfiles, useUpdateCrawlPolicy } from "@/hooks/use-resource-data"
import type { CrawlPolicyFilters, CrawlPolicyRecord, CrawlProfileRecord } from "@/types/resources"
import { PolicyForm, policyDraft, policyDraftError, policyValues, type PolicyDraft } from "./policy-form"

const defaultFilters: CrawlPolicyFilters = { matchPattern: "", enabled: "all", profileSlug: "", transport: "all" }

export function CrawlPoliciesPage() {
  const [filters, setFilters] = useState(defaultFilters)
  const [offset, setOffset] = useState(0)
  const [newDraft, setNewDraft] = useState<PolicyDraft | null>(null)
  const profilesQuery = useCrawlProfiles()
  const profiles = profilesQuery.data?.items ?? []
  const query = useCrawlPolicies(filters, { limit: RESOURCE_PAGE_SIZE, offset })
  const policies = query.data?.items ?? []
  const patchFilters = (patch: Partial<CrawlPolicyFilters>) => { setOffset(0); setFilters((current) => ({ ...current, ...patch })) }

  return <div className="flex min-h-0 w-full flex-col gap-4">
    <section className="flex flex-col gap-3 border-b pb-4"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><ShieldCheckIcon className="size-4 text-muted-foreground" /><h1 className="text-lg font-medium">Policies</h1><Badge variant="outline">{query.data?.total ?? 0}</Badge></div><div className="flex gap-2"><Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCwIcon className={query.isFetching ? "animate-spin" : ""} /> Refresh</Button><Button size="sm" disabled={!profiles.length} onClick={() => setNewDraft(policyDraft(undefined, profiles))}><PlusIcon /> New policy</Button></div></div>
      <div className="grid gap-2 lg:grid-cols-[minmax(16rem,1fr)_14rem_11rem]"><div className="relative"><FilterIcon className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground" /><Input className="pl-9" value={filters.matchPattern} placeholder="Find a website or path" onChange={(event) => patchFilters({ matchPattern: event.target.value })} /></div><Select value={filters.profileSlug || "all"} onValueChange={(value) => patchFilters({ profileSlug: value === "all" || value === null ? "" : value })}><SelectTrigger aria-label="Profile" className="w-full"><span>{filters.profileSlug ? profiles.find((profile) => profile.slug === filters.profileSlug)?.name ?? "Profile" : "All profiles"}</span></SelectTrigger><SelectContent><SelectItem value="all">All profiles</SelectItem>{profiles.map((profile) => <SelectItem key={profile.id} value={profile.slug}>{profile.name}</SelectItem>)}</SelectContent></Select><Select value={filters.enabled} onValueChange={(value) => value && patchFilters({ enabled: value as CrawlPolicyFilters["enabled"] })}><SelectTrigger aria-label="Status" className="w-full"><span>{filters.enabled === "enabled" ? "Enabled" : filters.enabled === "disabled" ? "Disabled" : "All statuses"}</span></SelectTrigger><SelectContent><SelectItem value="all">All statuses</SelectItem><SelectItem value="enabled">Enabled</SelectItem><SelectItem value="disabled">Disabled</SelectItem></SelectContent></Select></div>
    </section>
    <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80"><TableHeader><TableRow><TableHead>Applies to</TableHead><TableHead>Fetch with</TableHead><TableHead>Website limit</TableHead><TableHead>Status</TableHead><TableHead className="w-28">Action</TableHead></TableRow></TableHeader><TableBody>{policies.map((policy) => <PolicyRow key={policy.id} policy={policy} />)}{!query.isLoading && policies.length === 0 ? <TableRow><TableCell colSpan={5} className="h-28 text-center"><p className="font-medium">No matching policies</p><p className="mt-1 text-xs text-muted-foreground">Try a different filter or add a website rule.</p></TableCell></TableRow> : null}</TableBody></Table>
    <ResourcePagination total={query.data?.total ?? 0} limit={query.data?.limit ?? RESOURCE_PAGE_SIZE} offset={query.data?.offset ?? offset} isFetching={query.isFetching} onOffsetChange={setOffset} />
    <NewPolicyDialog draft={newDraft} onDraftChange={setNewDraft} profiles={profiles} onClose={() => setNewDraft(null)} />
  </div>
}

function PolicyRow({ policy }: { policy: CrawlPolicyRecord }) {
  const update = useUpdateCrawlPolicy(policy.id)
  const isDefault = policy.slug === "default"
  return <TableRow><TableCell className="max-w-[34rem]"><a href={`/crawl-policies/${policy.id}`} className="block min-w-0"><span className="block truncate font-medium text-link hover:underline">{coverageLabel(policy)}</span><span className="block truncate text-xs text-muted-foreground">{isDefault ? "Fallback when no specific website policy matches" : policy.match}</span></a></TableCell><TableCell><a href={`/crawl-profiles/${policy.profile.id}`} className="font-medium text-link hover:underline">{policy.profile.name}</a><span className="block text-xs text-muted-foreground">{transportLabel(policy)}</span></TableCell><TableCell><span className="font-medium tabular-nums">{policy.max_concurrency}</span><span className="block text-xs text-muted-foreground">simultaneous requests</span></TableCell><TableCell><Badge variant={policy.enabled ? "secondary" : "destructive"}>{policy.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}{policy.enabled ? "Enabled" : "Disabled"}</Badge></TableCell><TableCell><Button size="sm" variant="outline" disabled={isDefault || update.isPending} onClick={() => update.mutate({ enabled: !policy.enabled })}>{policy.enabled ? "Disable" : "Enable"}</Button></TableCell></TableRow>
}

function NewPolicyDialog({ draft, onDraftChange, profiles, onClose }: { draft: PolicyDraft | null; onDraftChange: (draft: PolicyDraft | null) => void; profiles: CrawlProfileRecord[]; onClose: () => void }) {
  const create = useCreateCrawlPolicy()
  const submit = () => {
    if (!draft) return
    const error = policyDraftError(draft)
    if (error) return toast.error(error)
    const values = policyValues(draft)
    create.mutate({ slug: `policy-${Date.now().toString(36)}`, ...values }, { onSuccess: (policy) => { onClose(); window.history.pushState(null, "", `/crawl-policies/${policy.id}`); window.dispatchEvent(new PopStateEvent("popstate")) } })
  }
  return <Dialog open={draft !== null} onOpenChange={(open) => { if (!open && !create.isPending) onClose() }}><DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-5xl"><DialogHeader><DialogTitle>New policy</DialogTitle><DialogDescription>Choose a website or path, then assign the profile Atlas should use there.</DialogDescription></DialogHeader>{draft ? <PolicyForm draft={draft} onChange={(value) => onDraftChange(value)} profiles={profiles} /> : null}<DialogFooter showCloseButton><Button disabled={!draft || create.isPending} onClick={submit}>{create.isPending ? "Creating…" : "Create policy"}</Button></DialogFooter></DialogContent></Dialog>
}

function coverageLabel(policy: CrawlPolicyRecord) { if (policy.host === "*") return "All websites and pages"; if (policy.path_prefix === "/" && policy.path_mode === "prefix") return `All pages on ${policy.host}`; return policy.path_mode === "exact" ? `${policy.host}${policy.path_prefix} only` : `${policy.host}${policy.path_prefix} and below` }
function transportLabel(policy: CrawlPolicyRecord) { return policy.profile.transport === "http" ? "Direct HTTP" : policy.profile.transport === "browser" ? "Browser" : "Firecrawl" }
