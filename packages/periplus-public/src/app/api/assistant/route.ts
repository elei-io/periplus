import { createAgentUIStreamResponse } from "ai"
import { createDiscoveryAgent } from "@/server/discovery-agent"
import type { QueryHelpers } from "@/types/query-helpers"
import { assistantMessages } from "@/server/assistant-input"

export const runtime = "nodejs"
export const maxDuration = 190
let active = 0

export async function POST(request: Request) {
  if (!process.env.OPENAI_API_KEY || !process.env.PERIPLUS_AI_MODEL || !process.env.PERIPLUS_QUERY_API_TOKEN) return Response.json({ detail: "The assistant is not configured yet. Try the SQL bench." }, { status: 503 })
  if (active >= 2) return Response.json({ detail: "The assistant is busy. Try again shortly." }, { status: 429, headers: { "Retry-After": "5" } })
  let messages
  try {
    const reader = request.body?.getReader()
    if (!reader) throw new Error("Missing body")
    const chunks: Uint8Array[] = []
    let size = 0
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      size += value.length
      if (size > 64_000) { await reader.cancel(); return Response.json({ detail: "Conversation is too long. Start a new chat." }, { status: 413 }) }
      chunks.push(value)
    }
    messages = assistantMessages(JSON.parse(Buffer.concat(chunks).toString("utf8")))
  } catch { return Response.json({ detail: "Please send a question or start a new chat." }, { status: 400 }) }
  // Admission is per process; production ingress owns aggregate rate limits.
  if (active >= 2) return Response.json({ detail: "The assistant is busy." }, { status: 429 })
  active++
  let released = false
  const release = () => { if (!released) { released = true; active-- } }
  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(180_000)])
  signal.addEventListener("abort", release, { once: true })
  try {
    const helperResponse = await fetch(new URL("/query/helpers", process.env.PERIPLUS_QUERY_URL ?? "http://127.0.0.1:8010"), {
      headers: { authorization: `Bearer ${process.env.PERIPLUS_QUERY_API_TOKEN}` },
      cache: "no-store", signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
    })
    if (!helperResponse.ok) throw new Error("SQL helper catalogue unavailable")
    const helpers: QueryHelpers = await helperResponse.json()
    return await createAgentUIStreamResponse({
      agent: createDiscoveryAgent(helpers), uiMessages: messages, abortSignal: signal, timeout: 180_000,
      sendReasoning: false,
      onEnd: () => { signal.removeEventListener("abort", release); release() },
      onError: () => { release(); return "The assistant could not finish. Try a narrower question or use the SQL bench." },
    })
  } catch { release(); return Response.json({ detail: "The assistant is temporarily unavailable." }, { status: 502 }) }
}
