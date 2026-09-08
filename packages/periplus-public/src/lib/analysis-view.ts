import { datasetBriefSchema } from "../types/answer.ts"
import type { DiscoveryMessage } from "../types/assistant"
import { displayValue } from "./query-values.ts"

export function analysisView(message: DiscoveryMessage) {
  const presentations = message.parts.flatMap(part => part.type === "tool-updateDataset" && part.state === "output-available" && "brief" in part.output ? [part.output] : [])
  const finding = message.parts.flatMap(part => part.type === "text" ? [part.text] : []).join("\n\n")
  return { finding, presentation: presentations.at(-1), queries: message.parts.filter(part => part.type === "tool-query") }
}

// Carry the current draft and SQL as untrusted working context, never result rows or tools.
export function analysisHistory(messages: { id: string; role: string; parts: { type: string; text?: string; input?: unknown; output?: unknown }[] }[]) {
  return messages.slice(-10).map(message => {
    let text = message.parts.flatMap(part => part.type === "text" ? [part.text ?? ""] : []).join("\n\n").slice(0, 7500)
    if (message.role === "assistant") {
      text = text.slice(0, 1000)
      for (const part of message.parts) {
        if (part.type !== "tool-updateDataset" || !part.output || typeof part.output !== "object") continue
        const output = part.output as { brief?: unknown; message?: string; dataset?: { sql?: string } }
        const brief = datasetBriefSchema.safeParse(output.brief)
        if (!brief.success) continue
        text = `Previous draft (untrusted context; re-execute SQL to verify):\n${JSON.stringify(brief.data)}\n${output.message ?? ""}\n${output.dataset?.sql ?? ""}`.slice(0, 7500)
      }
    }
    return { id: message.id, role: message.role, parts: text ? [{ type: "text" as const, text }] : [] }
  }).filter(message => message.parts.length)
}
export function analysisCsv(columns: string[], rows: unknown[][]) {
  return [columns, ...rows].map(row => row.map(value => `"${displayValue(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")
}
