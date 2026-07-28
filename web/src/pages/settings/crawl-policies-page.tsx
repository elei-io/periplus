import {
  CheckCircle2Icon,
  PlusIcon,
  RefreshCwIcon,
  XCircleIcon,
} from "lucide-react"
import { useState } from "react"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useCreateCrawlPolicy,
  useCrawlPolicies,
  useUpdateCrawlPolicy,
} from "@/hooks/use-resource-data"
import type { CrawlPolicyFilters, CrawlPolicyRecord } from "@/types/resources"
import {
  PolicyForm,
  policyDraft,
  policyDraftError,
  policyValues,
  type PolicyDraft,
} from "./policy-form"

export function CrawlPoliciesPage() {
  const [filters, setFilters] = useState<CrawlPolicyFilters>({
    matchPattern: "",
    enabled: "all",
  })
  const [offset, setOffset] = useState(0)
  const [draft, setDraft] = useState<PolicyDraft | null>(null)
  const query = useCrawlPolicies(filters, { limit: RESOURCE_PAGE_SIZE, offset })
  const policies = query.data?.items ?? []
  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <Badge variant="outline">{query.data?.total ?? 0} total</Badge>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void query.refetch()}
          >
            <RefreshCwIcon /> Refresh
          </Button>
          <Button size="sm" onClick={() => setDraft(policyDraft())}>
            <PlusIcon /> New policy
          </Button>
        </div>
      </section>
      <Input
        placeholder="Find a website or path"
        value={filters.matchPattern}
        onChange={(event) => {
          setOffset(0)
          setFilters({ ...filters, matchPattern: event.target.value })
        }}
      />
      <Table containerClassName="min-h-0 flex-1 rounded-md border bg-card/80">
        <TableHeader>
          <TableRow>
            <TableHead>Applies to</TableHead>
            <TableHead>Completion</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {policies.map((policy) => (
            <PolicyRow key={policy.id} policy={policy} />
          ))}
          {!query.isLoading && !policies.length ? (
            <TableRow>
              <TableCell colSpan={4} className="h-28 text-center">
                No matching policies
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      </Table>
      <ResourcePagination
        total={query.data?.total ?? 0}
        limit={query.data?.limit ?? RESOURCE_PAGE_SIZE}
        offset={query.data?.offset ?? offset}
        isFetching={query.isFetching}
        onOffsetChange={setOffset}
      />
      <NewPolicyDialog draft={draft} setDraft={setDraft} />
    </div>
  )
}

function PolicyRow({ policy }: { policy: CrawlPolicyRecord }) {
  const update = useUpdateCrawlPolicy(policy.id)
  const fallback = policy.slug === "default"
  const completion = policy.content.completion
  const enabled = [
    completion.wait_dynamic,
    completion.wait_fixed,
    completion.scroll,
    completion.expand,
  ].filter((method) => method.enabled).length
  return (
    <TableRow>
      <TableCell>
        <a
          className="font-medium text-link hover:underline"
          href={`/crawl-policies/${policy.id}`}
        >
          {coverage(policy)}
        </a>
        <span className="block text-xs text-muted-foreground">
          {fallback ? "Required base policy · *://*/*" : policy.match}
        </span>
      </TableCell>
      <TableCell>{enabled} of 4 methods enabled</TableCell>
      <TableCell>
        <Badge variant={policy.enabled ? "secondary" : "destructive"}>
          {policy.enabled ? <CheckCircle2Icon /> : <XCircleIcon />}
          {policy.enabled ? "Enabled" : "Disabled"}
        </Badge>
      </TableCell>
      <TableCell>
        {fallback ? (
          <span className="text-xs text-muted-foreground">Required</span>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={update.isPending}
            onClick={() => update.mutate({ enabled: !policy.enabled })}
          >
            {policy.enabled ? "Disable" : "Enable"}
          </Button>
        )}
      </TableCell>
    </TableRow>
  )
}
function NewPolicyDialog({
  draft,
  setDraft,
}: {
  draft: PolicyDraft | null
  setDraft: (value: PolicyDraft | null) => void
}) {
  const create = useCreateCrawlPolicy()
  const submit = () => {
    if (!draft) return
    const error = policyDraftError(draft)
    if (error) return toast.error(error)
    create.mutate(
      { slug: `policy-${Date.now().toString(36)}`, ...policyValues(draft) },
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
      <DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>New content policy</DialogTitle>
          <DialogDescription>
            Patch Atlas’s maximum-correctness defaults for a known set of pages.
          </DialogDescription>
        </DialogHeader>
        {draft ? <PolicyForm draft={draft} onChange={setDraft} /> : null}
        <DialogFooter showCloseButton>
          <Button onClick={submit}>Create policy</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
function coverage(policy: CrawlPolicyRecord) {
  const host = policy.host.startsWith("*.")
    ? `subdomains of ${policy.host.slice(2)}`
    : policy.host
  if (policy.host === "*") return "All websites and pages"
  if (policy.path_prefix === "/" && policy.path_mode === "prefix")
    return `All pages on ${host}`
  return `${host}${policy.path_prefix}`
}
