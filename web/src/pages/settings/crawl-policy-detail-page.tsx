import { ArrowLeftIcon, CheckCircle2Icon, RefreshCwIcon, SaveIcon, ShieldCheckIcon, Trash2Icon, XCircleIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useCrawlPolicy, useCrawlProfiles, useDeleteCrawlPolicy, useUpdateCrawlPolicy } from "@/hooks/use-resource-data"
import type { CrawlPolicyDetailRecord, CrawlProfileRecord } from "@/types/resources"
import { PolicyForm, policyDraft, policyDraftError, policyValues } from "./policy-form"

export function CrawlPolicyDetailPage({ policyId }: { policyId: string }) {
  const query = useCrawlPolicy(policyId)
  const profilesQuery = useCrawlProfiles()
  const policy = query.data
  if (query.isLoading) return <Centered>Loading policy…</Centered>
  if (!policy) return <Centered>Policy not found.</Centered>
  return <div className="flex min-h-0 w-full flex-col gap-4">
    <section className="flex flex-wrap items-start justify-between gap-3 border-b pb-4"><div className="flex min-w-0 items-start gap-3"><Button variant="ghost" size="icon-sm" nativeButton={false} render={<a href="/crawl-policies" />}><ArrowLeftIcon /></Button><div className="min-w-0"><div className="flex items-center gap-2"><ShieldCheckIcon className="size-4 text-muted-foreground" /><h1 className="truncate text-lg font-medium">{coverageLabel(policy)}</h1></div><div className="mt-1 flex flex-wrap gap-2"><Badge variant={policy.enabled ? "secondary" : "destructive"}>{policy.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}{policy.enabled ? "Enabled" : "Disabled"}</Badge><Badge variant="outline">{policy.profile.name}</Badge></div></div></div><Button variant="outline" size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}><RefreshCwIcon className={query.isFetching ? "animate-spin" : ""} /> Refresh</Button></section>
    <ExistingPolicyForm key={policy.updated_at} policy={policy} profiles={profilesQuery.data?.items ?? [policy.profile]} />
    <div className="border-t pt-3 text-xs text-muted-foreground">Policy key <span className="font-mono">{policy.slug}</span> · Updated {formatDate(policy.updated_at)}</div>
  </div>
}

function ExistingPolicyForm({ policy, profiles }: { policy: CrawlPolicyDetailRecord; profiles: CrawlProfileRecord[] }) {
  const update = useUpdateCrawlPolicy(policy.id)
  const remove = useDeleteCrawlPolicy(policy.id)
  const isDefault = policy.slug === "default"
  const [draft, setDraft] = useState(() => policyDraft(policy, profiles))
  const save = () => {
    const error = policyDraftError(draft, isDefault)
    if (error) return toast.error(error)
    update.mutate(policyValues(draft, isDefault))
  }
  const deletePolicy = () => {
    if (!window.confirm("Delete this policy? A less-specific rule will take over for these pages.")) return
    remove.mutate(undefined, { onSuccess: () => { window.history.pushState(null, "", "/crawl-policies"); window.dispatchEvent(new PopStateEvent("popstate")) } })
  }
  return <><PolicyForm draft={draft} onChange={setDraft} profiles={profiles} defaultPolicy={isDefault} /><div className="sticky bottom-3 z-10 flex items-center justify-between gap-3"><div>{!isDefault ? <Button variant="destructive" disabled={remove.isPending} onClick={deletePolicy}><Trash2Icon /> Delete policy</Button> : null}</div><Button className="shadow-lg" disabled={update.isPending} onClick={save}><SaveIcon /> {update.isPending ? "Saving…" : "Save policy"}</Button></div></>
}

function coverageLabel(policy: CrawlPolicyDetailRecord) { if (policy.host === "*") return "All websites and pages"; if (policy.path_prefix === "/" && policy.path_mode === "prefix") return `All pages on ${policy.host}`; return policy.path_mode === "exact" ? `${policy.host}${policy.path_prefix} only` : `${policy.host}${policy.path_prefix} and below` }
function Centered({ children }: { children: React.ReactNode }) { return <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">{children}</div> }
function formatDate(value: string) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) }
