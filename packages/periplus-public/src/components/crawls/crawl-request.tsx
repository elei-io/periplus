"use client"

import Link from "next/link"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useCrawlRequest } from "@/hooks/use-crawl-request"
import { extractApiError } from "@/lib/api"

export function CrawlRequest({ initialReceipt = null }: { initialReceipt?: string | null }) {
  const [url, setUrl] = useState("")
  const [receipt, setReceipt] = useState(initialReceipt)
  const { submission, progress } = useCrawlRequest(receipt)
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader><CardTitle>Submit a crawl request</CardTitle></CardHeader>
        <CardContent>
          <form className="flex flex-col gap-4" onSubmit={(event) => {
            event.preventDefault()
            submission.mutate(url, { onSuccess: (result) => {
              setReceipt(result.receipt)
              window.history.replaceState(null, "", `/crawl?receipt=${encodeURIComponent(result.receipt)}`)
            } })
          }}>
            <label htmlFor="crawl-url">Page URL</label>
            <Input id="crawl-url" type="url" required maxLength={8192} placeholder="https://example.com/" value={url} onChange={(event) => setUrl(event.target.value)} />
            <p>Periplus will acquire this page once. Links on the page will not be followed.</p>
            <Button type="submit" disabled={submission.isPending}>{submission.isPending ? "Submitting…" : "Request crawl"}</Button>
          </form>
        </CardContent>
      </Card>
      {receipt && <Card>
        <CardHeader><CardTitle>Crawl progress</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4" aria-live="polite">
          {progress.error ? <p role="alert">{extractApiError(progress.error)}</p> : <>
            <p>Status: {progress.data?.status.replaceAll("_", " ") ?? "Loading…"}</p>
            {progress.data && <p>{progress.data.request_count - progress.data.pending_request_count} of {progress.data.request_count} pages processed; {progress.data.failed_request_count} failed.</p>}
          </>}
          <p>Save this page’s address to check again. The receipt grants access to this request’s progress for seven days.</p>
          <p>Acquisition completes before ingestion and catalogue materialization. Catalogue results may appear later.</p>
          <Link href="/sql">Query the catalogue</Link>
        </CardContent>
      </Card>}
    </div>
  )
}
