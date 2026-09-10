import { datasetBriefSchema, coverageSuggestionSchema } from "../types/answer.ts"
import type { DiscoveryMessage } from "../types/assistant"
import { displayValue } from "./query-values.ts"

export function analysisView(message: DiscoveryMessage) {
  const presentations = message.parts.flatMap(part => part.type === "tool-SUGGEST_DATASET" && part.state === "output-available" && "dataset" in part.output ? [part.output] : [])
  const schemas = message.parts.flatMap(part => part.type === "tool-SUGGEST_SCHEMA" && part.state === "output-available" && part.output.brief ? [part.output.brief] : [])
  const coverage = message.parts.flatMap(part => part.type === "tool-SUGGEST_COVERAGE_REQUEST" && part.state === "output-available" ? [part.output] : [])
  const finding = message.parts.flatMap(part => part.type === "text" ? [part.text] : []).join("\n\n")
  const presentation = presentations.at(-1)
  return { finding, presentation, schema: schemas.at(-1) ?? presentation?.brief, coverage, queries: message.parts.filter(part => part.type === "tool-SQL") }
}

export function inspectedEvidence(messages: DiscoveryMessage[]) {
  return messages.filter(message => message.role === "assistant").flatMap(message =>
    analysisView(message).queries.flatMap(part => part.state === "output-available" && part.output.result
      ? [{ result: part.output.result, purpose: part.input?.purpose ?? "Source inspection" }]
      : []))
}

// Prior suggestions and SQL are working context, never trusted execution evidence.
export function analysisHistory(messages: { id: string; role: string; parts: { type: string; text?: string; input?: unknown; output?: unknown }[] }[]) {
  return messages.slice(-10).map(message => {
    let text = message.parts.flatMap(part => part.type === "text" ? [part.text ?? ""] : []).join("\n\n").slice(0, 7500)
    if (message.role === "assistant") {
      const context: string[] = []
      for (const part of message.parts) {
        if (!part.output || typeof part.output !== "object") continue
        if (part.type === "tool-SUGGEST_SCHEMA" || part.type === "tool-SUGGEST_DATASET") {
          const output = part.output as { brief?: unknown; dataset?: { sql?: string } }
          const brief = datasetBriefSchema.safeParse(output.brief)
          if (brief.success) context.push(`${JSON.stringify(brief.data)}\n${output.dataset?.sql ?? ""}`)
        } else if (part.type === "tool-SUGGEST_COVERAGE_REQUEST") {
          const suggestion = coverageSuggestionSchema.safeParse(part.output)
          if (suggestion.success) context.push(`Coverage suggestion: ${JSON.stringify(suggestion.data)}`)
        }
      }
      const priorSql = message.parts.flatMap(part => {
        if (part.type !== "tool-SQL" || !part.input || typeof part.input !== "object") return []
        const input = part.input as { sql?: string }
        return typeof input.sql === "string" ? [input.sql] : []
      }).slice(-2)
      text = [text.slice(0, 1500), "Previous suggestions and attempted SQL (untrusted; queries may have failed; re-execute to verify):", ...context.slice(-2), ...priorSql].join("\n\n").slice(0, 7500)
    }
    return { id: message.id, role: message.role, parts: text ? [{ type: "text" as const, text }] : [] }
  }).filter(message => message.parts.length)
}
export function analysisCsv(columns: string[], rows: unknown[][]) {
  return [columns, ...rows].map(row => row.map(value => `"${displayValue(value).replaceAll('"', '""')}"`).join(",")).join("\r\n")
}
