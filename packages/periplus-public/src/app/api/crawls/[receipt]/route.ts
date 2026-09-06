import { readReceipt } from "@/server/crawl-receipts"
import { periplusRequest, periplusErrorResponse, PeriplusError } from "@/server/periplus-client"
import { isCrawlProgress } from "@/types/crawls"

export async function GET(request: Request, context: RouteContext<"/api/crawls/[receipt]">) {
  try {
    const { receipt } = await context.params
    const id = readReceipt(receipt)
    if (!id) return Response.json({ detail: "Crawl receipt is invalid or expired." }, { status: 404 })
    const result = await periplusRequest(`/crawls/${id}`, { signal: request.signal })
    if (!isCrawlProgress(result)) throw new PeriplusError("Periplus returned incompatible crawl progress.", 502)
    return Response.json(result, { headers: { "Cache-Control": "no-store" } })
  } catch (error) {
    return periplusErrorResponse(error)
  }
}
