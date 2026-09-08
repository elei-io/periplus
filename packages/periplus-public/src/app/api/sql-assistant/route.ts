import { sqlAssistantInputSchema } from "@/types/sql-assistant"
import { admitPublic } from "@/server/public-access"
import { suggestSql } from "@/server/sql-assistant"
import { beginOperation } from "@/server/telemetry"

export const runtime = "nodejs"
export const maxDuration = 100
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
      if (size > 128_000) {
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
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(90_000)])
  try {
    const reply = await suggestSql(input, signal)
    finish("success")
    return Response.json(reply, { headers: { "cache-control": "no-store" } })
  } catch {
    finish(signal.aborted ? request.signal.aborted ? "cancelled" : "timeout" : "failed")
    return Response.json({ detail: signal.aborted ? "The SQL assistant timed out. Try a smaller change." : "The SQL assistant is temporarily unavailable. Your SQL has been kept." }, { status: 502 })
  } finally {
    active--
  }
}
