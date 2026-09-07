"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Globe, MessageSquare } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { extractApiError, responseJson } from "@/lib/api"
import type { Collection, CreateCollection } from "@/types/collections"
import { publicCollectionSpec } from "@/lib/collection-submission"

const pageOptions = [5, 25, 100, 500, 1000]
function pageLabel(n: number) { return n === 1000000 ? "1M+" : n >= 1000 ? `${n / 1000}k` : String(n) }

export function CollectionForm({ onCreated }: { onCreated: (id: string) => void }) {
  const cache = useQueryClient()
  const [kind, setKind] = useState<"url" | "description">("url")
  const [url, setUrl] = useState("")
  const [description, setDescription] = useState("")
  const [depth, setDepth] = useState(1)
  const [scope, setScope] = useState<"internal" | "external" | "both">("internal")
  const [maxPages, setMaxPages] = useState(25)
  const [sections, setSections] = useState("")
  const [frozen, setFrozen] = useState<CreateCollection | null>(null)
  const submit = useMutation({
    retry: false,
    mutationFn: async (payload: CreateCollection) => responseJson<Collection>(await fetch("/api/collections", {
      method: "POST", headers: { "content-type": "application/json" },
      signal: AbortSignal.timeout(35000), body: JSON.stringify(payload),
    })),
    onSuccess: result => {
      cache.setQueryData(["collection", result.id], result)
      void cache.invalidateQueries({ queryKey: ["collections"] })
      onCreated(result.id)
      toast.success("Collection submitted. Keep its link to follow updates.")
    },
    onError: error => toast.error(extractApiError(error)),
  })
  return <Card>
    <CardHeader><CardTitle><h2>Suggest a starting point</h2></CardTitle><CardDescription>Start with a URL, or describe the data you wish you could query.</CardDescription></CardHeader>
    <CardContent>
      <form className="flex flex-col gap-6" onSubmit={event => { event.preventDefault(); if (frozen) return; try { const payload = { id: crypto.randomUUID(), specification: publicCollectionSpec({ kind, input: kind === "url" ? url : description, depth, scope, maxPages, sections }), priority: 0 }; setFrozen(payload); submit.mutate(payload) } catch (error) { toast.error(extractApiError(error)) } }}>
        <fieldset disabled={frozen !== null} className="flex min-w-0 flex-col gap-6">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Request type">
          <Button type="button" variant={kind === "url" ? "secondary" : "outline"} aria-pressed={kind === "url"} onClick={() => setKind("url")}><Globe />Start with a URL</Button>
          <Button type="button" variant={kind === "description" ? "secondary" : "outline"} aria-pressed={kind === "description"} onClick={() => setKind("description")}><MessageSquare />Describe the data</Button>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="coverage-input">{kind === "url" ? "Public starting URL" : "What would you like to explore?"}</label>
          {kind === "url" ? <Input id="coverage-input" type="url" required maxLength={4000} placeholder="https://example.com" value={url} onChange={event => setUrl(event.target.value)} /> : <Textarea id="coverage-input" required maxLength={4000} rows={4} placeholder="For example: Finnish companies building industrial robots, including their products and technical specifications." value={description} onChange={event => setDescription(event.target.value)} />}
          <CardDescription>{kind === "url" ? "The starting page is depth 0. Each additional level follows another link." : "Tell us the topic, region, or kinds of pages you need. Starting URLs will be chosen automatically using search."}</CardDescription>
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-2"><label id="depth-label">How far to follow links</label>
            <Select value={depth} onValueChange={value => { if (value !== null) setDepth(value) }}>
              <SelectTrigger className="w-full min-h-11" aria-labelledby="depth-label"><SelectValue>{depth === 0 ? "0 · Starting pages only" : `${depth} ${depth === 1 ? "level" : "levels"}`}</SelectValue></SelectTrigger>
              <SelectContent>{[0, 1, 2].map(value => <SelectItem key={value} value={value} disabled={value > 2}>{value === 0 ? "0 · Starting pages only" : `${value} ${value === 1 ? "level" : "levels"}`}{value > 2 ? " · Unavailable" : ""}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2"><label id="scope-label">Follow links</label>
            <Select value={scope} disabled={depth === 0} onValueChange={value => { if (value) setScope(value) }}>
              <SelectTrigger className="w-full min-h-11" aria-labelledby="scope-label"><SelectValue>{depth === 0 ? "No links followed" : scope === "internal" ? "Internal only" : scope === "external" ? "External only" : "Internal & external"}</SelectValue></SelectTrigger>
              <SelectContent><SelectItem value="internal">Internal only</SelectItem><SelectItem value="external">External only</SelectItem><SelectItem value="both">Internal & external</SelectItem></SelectContent>
            </Select>
          </div>
          <div className="flex flex-col gap-2"><label id="pages-label">Maximum pages</label>
            <Select value={maxPages} onValueChange={value => { if (value !== null) setMaxPages(value) }}>
              <SelectTrigger className="w-full min-h-11" aria-labelledby="pages-label"><SelectValue>{pageLabel(maxPages)} pages</SelectValue></SelectTrigger>
              <SelectContent>{pageOptions.map(value => <SelectItem key={value} value={value} disabled={value > 1000}>{pageLabel(value)} pages{value > 1000 ? " · Unavailable" : ""}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
        <CardDescription>Internal means within each observed page’s site, including subdomains; external means other sites. The page budget covers the whole request, including starting pages. Public requests support up to 2 link steps and 1,000 pages.</CardDescription>
        <details>
          <summary>Limit collection to specific sections (optional)</summary>
          <div className="flex flex-col gap-2">
            <label htmlFor="coverage-sections">Allowed URL sections</label>
            <Textarea id="coverage-sections" rows={3} maxLength={10000} value={sections} onChange={event => setSections(event.target.value)} placeholder="https://duckdb.org/docs/stable/" aria-describedby="coverage-sections-help" />
            <CardDescription id="coverage-sections-help">One URL per line, up to 10. Starting pages and followed links must match an exact host and section path, or a descendant path. Other hosts and sections are excluded. Query strings are ignored when matching. These limits apply in addition to your link choice; they do not restrict redirects or page resources.</CardDescription>
          </div>
        </details>
        <Alert><AlertDescription>Requests are picked up automatically. Descriptions are sent to our AI and search providers to find starting pages. Observations are limited by the options above; inclusion is not guaranteed. All request details are public—please leave out private URLs, credentials, and personal information.</AlertDescription></Alert>
        </fieldset>
        {submit.error && <Alert variant="destructive"><AlertDescription>{extractApiError(submit.error)}</AlertDescription></Alert>}
        {!frozen && <Button className="self-start" type="submit">Submit request<ArrowUpRight /></Button>}
        {frozen && <div className="flex flex-col gap-3"><a className="underline break-all" href={`/observatory?request=${frozen.id}#request`}>View request {frozen.id}</a>{submit.isPending && <p role="status">Submitting…</p>}{submit.isError && <><p>Submission was not confirmed. Retrying preserves the same intent and identity.</p><Button type="button" className="self-start" onClick={() => submit.mutate(frozen)}>Retry same request</Button></>}<a className="underline" href="/observatory">Suggest another starting point</a></div>}
      </form>
    </CardContent>
  </Card>
}
