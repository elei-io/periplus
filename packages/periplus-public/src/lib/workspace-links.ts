import type { DatasetBrief } from "../types/answer"

export function discoverLink(question: string) {
  return `/discover?${new URLSearchParams({ question })}`
}

export function buildLink(question: string, draft?: DatasetBrief) {
  return `/build?${new URLSearchParams({ question, ...(draft ? { draft: JSON.stringify(draft) } : {}) })}`
}

export function sqlBuildLink(result: { sql: string; parameters?: unknown[]; columns: string[]; types: string[] }) {
  return buildLink(`Help turn this query into a dataset. Confirm the row meaning, source scope, and required columns with me. Preserve the query and parameters as a starting point.\n\nSQL:\n${result.sql}\n\nParameters: ${JSON.stringify(result.parameters ?? [])}`, {
    title: "Dataset from SQL", grain: "", population: "",
    fields: result.columns.map((name, index) => ({ name, type: result.types[index], nullable: true, meaning: "" })),
  })
}
