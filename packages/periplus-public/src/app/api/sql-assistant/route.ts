import { sqlAssistantInputSchema } from "@/types/sql-assistant"
import { admitPublic } from "@/server/public-access"
import { suggestSql } from "@/server/sql-assistant"
import { beginOperation } from "@/server/telemetry"

export const runtime = "nodejs"
export const maxDuration = 310
let active = 0

export async function POST(request: Request) {
  const finish = beginOperation("assistant")
  if (!process.env.OPENAI_API_KEY || !process.env.PERIPLUS_AI_MODEL || !process.env.PERIPLUS_QUERY_API_TOKEN) {
    finish("unconfigured")
    return Response.json({ detail: "The SQL assistant is not configured yet." }, { status: 503 })
  }
  const busy = () => {
    finish("rejected")
    return Response.json({ detail: "The SQL assistant is busy. Try again shortly." }, { status: 429, headers: { "Retry-After": "5" } })
  }
  if (active >= 2) return busy()
  let input
  try {
    const reader = request.body?.getReader()
    if (!reader) throw new Error("Missing body")
    const chunks: Uint8Array[] = []
    let size = 0
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.length
      if (size > 256_000) {
        await reader.cancel()
        finish("invalid")
        return Response.json({ detail: "This SQL conversation is too large. Shorten the query or start again." }, { status: 413 })
      }
      chunks.push(value)
    }
    input = sqlAssistantInputSchema.parse(JSON.parse(Buffer.concat(chunks).toString("utf8")))
  } catch {
    finish("invalid")
    return Response.json({ detail: "Provide a request, SQL of at most 20,000 characters, and parameters as a JSON array." }, { status: 400 })
  }
  const denial = await admitPublic("assistant", request.signal)
  if (denial) { finish(denial.status >= 500 ? "failed" : "rejected"); return denial }
  if (active >= 2) return busy()
  active++
  const cancellation = new AbortController()
  const signal = AbortSignal.any([request.signal, cancellation.signal, AbortSignal.timeout(300_000)])
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    async start(controller) {
      const emit = (event: import("@/types/sql-assistant").SqlAssistantEvent) => {
        if (!cancellation.signal.aborted) controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"))
      }
      try {
        const reply = await suggestSql(input, signal, emit)
        emit({ type: "result", reply })
        finish("success")
      } catch {
        finish(signal.aborted ? cancellation.signal.aborted || request.signal.aborted ? "cancelled" : "timeout" : "failed")
        emit({ type: "error", message: signal.aborted ? "The SQL assistant timed out. Try a smaller change." : "The SQL assistant is temporarily unavailable. Your SQL has been kept." })
      } finally {
        active--
        if (!cancellation.signal.aborted) controller.close()
      }
    },
    cancel() { cancellation.abort() },
  })
  return new Response(stream, { headers: { "content-type": "application/x-ndjson", "cache-control": "no-store", "x-accel-buffering": "no" } })
}
