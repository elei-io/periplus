"use client"

import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, Check, Globe2, RefreshCw, SlidersHorizontal, Sprout } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Card, CardHeader, CardContent } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/accordion"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { CollectionDepth } from "@/components/collection-depth"
import { usePublicAccess } from "@/hooks/use-public-access"
import { ApiError, extractApiError, responseJson } from "@/lib/api"
import { captureAnalytics } from "@/lib/analytics"
import { rememberCoverageSubmission } from "@/lib/coverage-analytics"
import type { Collection, CreateCollection, RepeatInterval } from "@/types/collections"
import { createRequestId } from "@/lib/request-id"
import { publicCollectionSpec } from "@/lib/collection-submission"
import "./collection-form.css"

const repeatChoices: { value: RepeatInterval | null; label: string; summary: string }[] = [
  { value: null, label: "Just once", summary: "One-time collection" },
  { value: 86400, label: "Every day", summary: "Repeats every day" },
  { value: 604800, label: "Every week", summary: "Repeats every week" },
  { value: 2592000, label: "Every 30 days", summary: "Repeats every 30 days" },
]

export function CollectionForm({ onCreated, initialDescription }: { onCreated: (id: string) => void; initialDescription?: string }) {
  const cache = useQueryClient()
  const access = usePublicAccess("crawl")
  const options = access.data?.crawl
  const [kind, setKind] = useState<"url" | "description">(initialDescription ? "description" : "url")
  const [url, setUrl] = useState("")
  const [description, setDescription] = useState(initialDescription ?? "")
  const [chosenDepth, setDepth] = useState<number>()
  const depth = chosenDepth ?? options?.default_max_depth ?? 0
  const [chosenLinks, setMaxLinks] = useState<number>()
  const maxLinks = chosenLinks ?? options?.default_follow_link_limit ?? 0
  const [scope, setScope] = useState<"internal" | "external" | "both">("internal")
  const [chosenPages, setMaxPages] = useState<number>()
  const maxPages = chosenPages ?? options?.default_page_budget ?? 0
  const [chosenRetention, setRetention] = useState<number | null | undefined>()
  const retention = chosenRetention === undefined ? options?.default_retention_seconds ?? null : chosenRetention
  const [repeat, setRepeat] = useState<RepeatInterval | null>(null)
  const frequency = repeatChoices.find(choice => choice.value === repeat)!
  const validOptions = !!options && options.page_budgets.includes(maxPages) && options.max_depths.includes(depth) && options.follow_link_limits.includes(maxLinks) && options.retention_seconds.includes(retention)
  const [sections, setSections] = useState("")
  const [frozen, setFrozen] = useState<CreateCollection | null>(null)
  const disabled = frozen !== null || !access.enabled
  const submit = useMutation({
    retry: false,
    mutationFn: async (payload: CreateCollection) => responseJson<Collection>(await fetch("/api/collections", {
      method: "POST", headers: { "content-type": "application/json" },
      signal: AbortSignal.timeout(35000), body: JSON.stringify(payload),
    })),
    onSuccess: (result, payload) => {
      cache.setQueryData(["collection", result.id], result)
      void cache.invalidateQueries({ queryKey: ["collections"] })
      onCreated(result.id)
      toast.success(payload.repeat_interval_seconds ? `Collection requested. ${frequency.summary}.` : "Collection requested. Keep its link to follow progress.")
      rememberCoverageSubmission(result.id)
      captureAnalytics("coverage_request_submitted", { request_id: result.id, kind: payload.specification.seed_description ? "description" : "url" })
    },
    onError: error => {
      access.onDenied(error)
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) setFrozen(null)
      toast.error(extractApiError(error))
    },
  })
  return <Card className="collection-composer">
    <CardHeader className="collection-composer-header">
      <div className="collection-eyebrow"><Sprout aria-hidden="true" />GROW THE COLLECTION</div>
      <h2>Add a little more of the web.</h2>
      <p>Choose a starting page. We’ll follow its links and collect pages for everyone to explore.</p>
    </CardHeader>
    <CardContent>
      <form className="flex flex-col gap-6" onSubmit={event => {
        event.preventDefault()
        if (frozen || !access.enabled || !validOptions) return
        try {
          const payload: CreateCollection = { id: createRequestId(), specification: publicCollectionSpec({ kind, input: kind === "url" ? url : description, depth, scope, maxPages, maxLinks, sections, retentionSeconds: retention }), priority: 0, repeat_interval_seconds: repeat }
          setFrozen(payload)
          submit.mutate(payload)
        } catch (error) { toast.error(extractApiError(error)) }
      }}>
        {access.message && <Alert><AlertDescription role="status">{access.message}</AlertDescription></Alert>}
        {options && !validOptions && <Alert variant="destructive"><AlertDescription>Available choices have changed. Choose a new depth or open advanced options to update the highlighted settings.</AlertDescription></Alert>}
        <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-7">
          <div className="collection-start flex flex-col gap-2">
            <label htmlFor="coverage-input">{kind === "url" ? "Where should we start?" : "Which sources are you looking for?"}</label>
            {kind === "url" ? <div className="collection-url"><Globe2 aria-hidden="true" /><Input id="coverage-input" type="text" inputMode="url" autoComplete="url" autoCapitalize="none" spellCheck={false} required maxLength={4000} placeholder="example.com" value={url} onChange={event => setUrl(event.target.value)} aria-describedby="coverage-input-help" /></div>
              : <Textarea id="coverage-input" required maxLength={4000} rows={3} placeholder="For example, Finnish robotics companies and their product pages." value={description} onChange={event => setDescription(event.target.value)} aria-describedby="coverage-input-help" />}
            <p id="coverage-input-help" className="collection-help">{kind === "url" ? "A website, an article, or a section you’d like to explore." : "We’ll use AI and search to find starting pages. Your description is shared with those providers."}</p>
          </div>
          <CollectionDepth depth={depth} options={options?.max_depths ?? []} onChange={setDepth} disabled={disabled} multipleSeeds={kind === "description"} />
          <Accordion defaultValue={initialDescription ? ["advanced"] : []} className="collection-advanced">
            <AccordionItem value="advanced">
              <AccordionTrigger><span className="collection-advanced-title"><SlidersHorizontal aria-hidden="true" /><span>Advanced options<small>Updates, page limits, and more</small></span></span></AccordionTrigger>
              <AccordionContent>
                <div className="collection-advanced-fields">
                  <div className="collection-repeat">
                    <div className="collection-field-heading"><div><label id="repeat-label">Check for updates</label><p>Collect once, or come back for a fresh look.</p></div><RefreshCw aria-hidden="true" /></div>
                    <Select value={repeat === null ? "once" : String(repeat)} disabled={disabled} onValueChange={value => { if (value !== null) setRepeat(value === "once" ? null : Number(value) as RepeatInterval) }}>
                      <SelectTrigger className="w-full" aria-labelledby="repeat-label" aria-describedby="repeat-help"><SelectValue>{frequency.label}</SelectValue></SelectTrigger>
                      <SelectContent>{repeatChoices.map(choice => <SelectItem key={choice.value ?? "once"} value={choice.value === null ? "once" : String(choice.value)}>{choice.label}</SelectItem>)}</SelectContent>
                    </Select>
                    <p id="repeat-help" className="collection-help">{repeat ? "The first collection starts as soon as possible, then repeats at this interval. Busy runs are skipped; timing depends on site limits and availability. The Periplus team can pause repeat collections." : "We’ll collect these pages once. You can request another collection anytime."}</p>
                  </div>
                  <div className="grid gap-5 sm:grid-cols-2">
                    <div className="flex flex-col gap-2"><label id="pages-label">Pages per collection</label>
                      <Select value={maxPages} disabled={disabled} onValueChange={value => { if (value !== null) setMaxPages(value) }}>
                        <SelectTrigger className="w-full" aria-labelledby="pages-label" aria-invalid={!!options && !options.page_budgets.includes(maxPages)}><SelectValue>Up to {maxPages.toLocaleString()} pages</SelectValue></SelectTrigger>
                        <SelectContent>{(options?.page_budgets ?? []).map(value => <SelectItem key={value} value={value}>Up to {value.toLocaleString()} pages</SelectItem>)}</SelectContent>
                      </Select><p className="collection-help">A total limit, including starting pages.</p>
                    </div>
                    <div className="flex flex-col gap-2"><label id="scope-label">Where links can lead</label>
                      <Select value={scope} disabled={disabled || depth === 0} onValueChange={value => { if (value) setScope(value) }}>
                        <SelectTrigger className="w-full" aria-labelledby="scope-label"><SelectValue>{depth === 0 ? "No links followed" : scope === "internal" ? "Stay on the same site" : scope === "external" ? "Other sites only" : "Any website"}</SelectValue></SelectTrigger>
                        <SelectContent><SelectItem value="internal">Stay on the same site</SelectItem><SelectItem value="external">Other sites only</SelectItem><SelectItem value="both">Any website</SelectItem></SelectContent>
                      </Select><p className="collection-help">{depth === 0 ? "Choose a greater depth to follow links." : "Applies to each page we visit. Same site includes subdomains."}</p>
                    </div>
                    <div className="flex flex-col gap-2"><label id="links-label">Links to try from each page</label>
                      <Select value={maxLinks} disabled={disabled || depth === 0} onValueChange={value => { if (value !== null) setMaxLinks(value) }}>
                        <SelectTrigger className="w-full" aria-labelledby="links-label" aria-invalid={!!options && !options.follow_link_limits.includes(maxLinks)}><SelectValue>{maxLinks.toLocaleString()} links</SelectValue></SelectTrigger>
                        <SelectContent>{(options?.follow_link_limits ?? []).map(value => <SelectItem key={value} value={value}>{value.toLocaleString()} links</SelectItem>)}</SelectContent>
                      </Select><p className="collection-help">We’ll still stay within your total page limit.</p>
                    </div>
                    <div className="flex flex-col gap-2"><label id="retention-label">How long to keep results</label>
                      <Select value={retention == null ? "forever" : String(retention)} disabled={disabled} onValueChange={value => { if (value !== null) setRetention(value === "forever" ? null : Number(value)) }}>
                        <SelectTrigger className="w-full" aria-labelledby="retention-label" aria-invalid={!!options && !options.retention_seconds.includes(retention)}><SelectValue>{retention ? `${retention / 86400} days` : "No expiry"}</SelectValue></SelectTrigger>
                        <SelectContent>{(options?.retention_seconds ?? []).map(value => <SelectItem key={value ?? "forever"} value={value == null ? "forever" : String(value)}>{value ? `${value / 86400} days` : "No expiry"}</SelectItem>)}</SelectContent>
                      </Select><p className="collection-help">Counted from when each collection finishes. After that, results may be removed.</p>
                    </div>
                  </div>
                  <div className="flex flex-col gap-2"><label htmlFor="coverage-sections">Only explore these sections <span className="collection-optional">(optional)</span></label>
                    <Textarea id="coverage-sections" rows={2} maxLength={10000} value={sections} onChange={event => setSections(event.target.value)} placeholder="https://example.com/articles/" aria-describedby="coverage-sections-help" />
                    <p id="coverage-sections-help" className="collection-help">One full URL per line, up to 10. Starting pages and links must be in these sections or their subsections. Redirects may lead elsewhere.</p>
                  </div>
                  <div className="collection-source-mode"><div><label id="source-mode-label">Don’t have a starting URL?</label><p className="collection-help">Describe the sources and we’ll look for them.</p></div><Button type="button" variant="outline" aria-pressed={kind === "description"} onClick={() => setKind(kind === "url" ? "description" : "url")}>{kind === "url" ? "Use a description" : "Use a URL"}</Button></div>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </fieldset>
        <div className="collection-footer">
          {options && <div className="collection-plan" aria-live="polite"><span><Check aria-hidden="true" />{kind === "url" && depth === 0 ? "Starting page only" : `Up to ${maxPages.toLocaleString()} pages`}</span><span>{frequency.summary}</span>{depth > 0 && <span>{scope === "internal" ? "Stays on each site" : scope === "external" ? "Follows links to other sites" : "Can visit other sites"}</span>}{sections.trim() && <span>Selected sections only</span>}</div>}
          <p className="collection-public-note">Your request and collected pages are public. Please use public URLs and leave out personal information.</p>
          {submit.error && <Alert variant="destructive"><AlertDescription>{extractApiError(submit.error)}</AlertDescription></Alert>}
          {!frozen && <Button size="lg" type="submit" disabled={!access.enabled || !validOptions} className="collection-submit">{repeat ? "Start & keep updated" : "Start collecting"}<ArrowRight aria-hidden="true" /></Button>}
          {frozen && <div className="flex flex-col gap-3">
            <a className="underline" href={`/coverage?request=${frozen.id}#request`}>Follow your collection</a>
            {submit.isPending && <p role="status">Sending your request…</p>}
            {submit.isError && <><p className="collection-help">We couldn’t confirm your request. It’s safe to retry; you won’t create a second collection or schedule.</p><Button type="button" disabled={!access.retryEnabled} onClick={() => submit.mutate(frozen)}>Try again</Button></>}
          </div>}
        </div>
      </form>
    </CardContent>
  </Card>
}
