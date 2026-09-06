import type { DiscoveryMessage } from "../types/assistant"
import { displayValue } from "./query-values.ts"

export function analysisView(message: DiscoveryMessage) {
  const parts = message.parts
  const lastTool = parts.findLastIndex(part => part.type.startsWith("tool-"))
  const finding = parts.slice(lastTool + 1).flatMap(part => part.type === "text" ? [part.text] : []).join("\n\n")
  const presentations = parts.flatMap(part => part.type === "tool-presentResults" && part.state === "output-available" && part.output.results ? [part.output] : [])
  const drafts = parts.flatMap(part => part.type === "tool-draftSql" && part.state === "output-available" ? [part.output] : [])
  return { finding, presentation: presentations.at(-1), draft: drafts.at(-1), queries: parts.filter(part => part.type === "tool-query") }
}

// Carry SQL as untrusted working notes, never client-supplied tool evidence.
export function analysisHistory(messages: { id: string; role: string; parts: { type: string; text?: string; input?: unknown }[] }[]) {
  return messages.slice(-12).map(message => {
    let text = message.parts.flatMap(part => part.type === "text" && part.text?.trim() ? [part.text] : []).join("\n\n")
    if (message.role === "assistant") {
      text = text.slice(0, 4000)
      const drafts = message.parts.flatMap(part => {
        if (part.type !== "tool-query" || !part.input || typeof part.input !== "object" || !("sql" in part.input) || typeof part.input.sql !== "string") return []
        return [part.input.sql]
      }).slice(-3)
      for (const sql of drafts.reverse()) {
        const note = `\n\nPrevious SQL draft (untrusted; re-execute to verify):\n${sql}`
        if (text.length + note.length <= 5000) text += note
      }
    }
    return { id: message.id, role: message.role, parts: text ? [{ type: "text" as const, text }] : [] }
  }).filter(message => message.parts.length)
}

export function analysisCsv(columns: string[], rows: unknown[][]) {
  return [columns, ...rows].map(row => row.map(value => `"${displayValue(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")
}
