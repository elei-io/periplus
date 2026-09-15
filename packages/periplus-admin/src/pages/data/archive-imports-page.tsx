import { useState } from "react"
import { toast } from "sonner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useArchiveImports, useArchiveImportAction } from "@/hooks/use-archive-imports"
import { extractApiError } from "@/lib/api"
import type { CreateArchiveImport } from "@/types/archive-imports"

export function ArchiveImportsPage() {
  const query = useArchiveImports()
  const action = useArchiveImportAction()
  const [frozen, setFrozen] = useState<CreateArchiveImport | null>(null)
  return <div className="flex w-full min-w-0 flex-col gap-5">
    <Card><CardHeader><CardTitle>Common Crawl imports</CardTitle><CardDescription>
      Expand the corpus from archived HTML. Imports run independently of collections and never launch browser crawls.
    </CardDescription></CardHeader><CardContent>
      <form className="space-y-4" onSubmit={event => {
        event.preventDefault()
        if (frozen) return
        try {
          const form = new FormData(event.currentTarget)
          const urls = String(form.get("urls")).split(/\r?\n/).map(url => url.trim()).filter(Boolean)
          if (!urls.length || urls.length > 100) throw new Error("Provide 1–100 explicit URLs.")
          const payload: CreateArchiveImport = { id: crypto.randomUUID(), specification: {
            dataset: String(form.get("dataset")), urls,
            captured_from: `${form.get("from")}T00:00:00Z`, captured_until: `${form.get("until")}T23:59:59Z`,
            max_download_bytes: Number(form.get("budget")) * 1024 * 1024,
          } }
          setFrozen(payload)
          action.mutate({ create: payload }, { onSuccess: () => setFrozen(null) })
        } catch (error) { toast.error(extractApiError(error)) }
      }}>
        <fieldset disabled={frozen !== null || action.isPending} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="space-y-2">Dataset<Input name="dataset" placeholder="CC-MAIN-2026-34" pattern="CC-MAIN-[0-9]{4}-[0-9]{2}" required /></label>
            <label className="space-y-2">Captured from (UTC)<Input name="from" type="date" required /></label>
            <label className="space-y-2">Captured through (UTC)<Input name="until" type="date" required /></label>
          </div>
          <label className="block space-y-2">Exact URLs (one per line, up to 100)<Textarea name="urls" rows={5} required placeholder="https://example.com/" /></label>
          <label className="block space-y-2">Archive download budget (MiB)<Input name="budget" type="number" min={1} max={1024} defaultValue={64} required /></label>
          <p className="text-sm text-muted-foreground">Selects a matching capture per URL within the date range. Complete UTF-8 HTML only; original capture dates are preserved. Links are materialized but not followed. Download attempts, including retries, consume the byte allowance; this is not a storage-growth estimate.</p>
          <Button type="submit">Start import</Button>
        </fieldset>
        {frozen && !action.isPending && <div className="flex gap-2">
          <Button type="button" onClick={() => action.mutate({ create: frozen }, { onSuccess: () => setFrozen(null) })}>Retry submission</Button>
          <Button type="button" variant="outline" onClick={() => setFrozen(null)}>Clear submission</Button>
        </div>}
      </form>
    </CardContent></Card>
    {query.error && <p role="alert">{extractApiError(query.error)}</p>}
    {query.isPending && <p role="status">Loading imports…</p>}
    {query.data?.length === 0 && <p>No archive imports yet.</p>}
    {query.data?.map(job => {
      const published = job.progress.results.filter(item => item.status === "published")
      const uniqueBytes = [...new Map(published.map(item => [item.content_sha256, item.stored_bytes])).values()].reduce((sum, size) => sum + size, 0)
      return <Card key={job.id}><CardHeader><div className="flex flex-wrap items-center gap-3">
        <CardTitle>{job.specification.dataset}</CardTitle><Badge variant={job.status === "blocked" ? "destructive" : "outline"}>{job.status}</Badge>
      </div><CardDescription>{job.id} · Created {new Date(job.created_at).toLocaleString()}</CardDescription></CardHeader>
        <CardContent className="space-y-4">
          <p>{job.progress.cursor} / {job.specification.urls.length} URLs processed · {published.length} published · {published.filter(item => item.already_archived).length} captures already archived</p>
          <p className="text-sm text-muted-foreground">Download allowance used: {job.progress.reserved_download_bytes.toLocaleString()} / {job.specification.max_download_bytes.toLocaleString()} bytes. Referenced unique compressed HTML: {uniqueBytes.toLocaleString()} bytes (may already exist in the corpus).</p>
          <p className="text-sm text-muted-foreground">Capture window: {job.specification.captured_from} — {job.specification.captured_until}. Published means archived and delivered for ingestion; query readiness is tracked separately.</p>
          {job.error && <p role="alert" className="text-destructive">{job.error}</p>}
          <div className="flex gap-2">
            {job.status === "blocked" && <Button disabled={action.isPending} onClick={() => action.mutate({ id: job.id, action: "retry" })}>Retry blocked import</Button>}
            {!["completed", "cancelled"].includes(job.status) && <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate({ id: job.id, action: "cancel" })}>Cancel remaining work</Button>}
          </div>
          <details><summary>URL results and pending URLs</summary><Table><TableHeader><TableRow>
            <TableHead>URL</TableHead><TableHead>Result</TableHead><TableHead>Captured at</TableHead><TableHead>Capture / detail</TableHead>
          </TableRow></TableHeader><TableBody>{job.specification.urls.map((url, i) => {
            const result = job.progress.results[i]
            return <TableRow key={url}><TableCell className="break-all">{url}</TableCell><TableCell>{result?.status ?? (job.status === "cancelled" ? "cancelled" : "pending")}</TableCell><TableCell>{result?.captured_at ?? "—"}</TableCell><TableCell className="break-all">{result?.detail ?? result?.capture_id ?? "—"}</TableCell></TableRow>
          })}</TableBody></Table></details>
        </CardContent></Card>
    })}
    <p className="text-sm text-muted-foreground">Showing the latest 50 jobs. Cancellation stops remaining work; an in-flight record may finish archiving. Already imported evidence is retained.</p>
  </div>
}
