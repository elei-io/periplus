import { createReceipt, requireReceiptConfiguration } from "@/server/crawl-receipts"
import { admitSubmission, submissionUrl, isSameOrigin } from "@/server/crawl-submission"
import { periplusRequest, periplusErrorResponse, PeriplusError } from "@/server/periplus-client"

export async function POST(request: Request) {
  if (!isSameOrigin(request)) {
    return Response.json({ detail: "Submit from the Periplus website." }, { status: 403 })
  }
  if (Number(request.headers.get("content-length")) > 10_000) {
    return Response.json({ detail: "Request is too large." }, { status: 413 })
  }
  const url = submissionUrl(await request.json().catch(() => null))
  if (!url) return Response.json({ detail: "Enter one HTTP or HTTPS URL." }, { status: 400 })
  try {
    requireReceiptConfiguration()
    if (!admitSubmission()) return Response.json({ detail: "Crawl submissions are busy. Try again in a minute." }, { status: 429, headers: { "Retry-After": "60" } })
    const result = await periplusRequest("/crawls/", {
      method: "POST",
      body: JSON.stringify({ urls: [url], depth: 0, max_crawls: 1, max_run_seconds: 300 }),
      signal: request.signal,
    })
    if (!result || typeof result !== "object" || !("run_id" in result) || typeof result.run_id !== "string") {
      throw new PeriplusError("Periplus returned an incompatible crawl response.", 502)
    }
    return Response.json({ receipt: createReceipt(result.run_id), status: "queued" }, { status: 202, headers: { "Cache-Control": "no-store" } })
  } catch (error) {
    return periplusErrorResponse(error)
  }
}
