import { z } from "zod"

export const datasetBriefSchema = z.object({
  title: z.string().min(1).max(120),
  grain: z.string().min(1).max(300).describe("What one row represents."),
  fields: z.array(z.object({
    name: z.string().regex(/^[a-z_][a-z0-9_]*$/).max(60),
    type: z.string().min(1).max(60).describe("Exact executed DuckDB type."),
    meaning: z.string().min(1).max(160),
    nullable: z.boolean(),
  })).min(1).max(16),
  population: z.string().min(1).max(500).describe("Included sources, coverage and observation selection; unresolved until inspected."),
})

export const answerSchema = z.object({
  status: z.enum(["draft", "sample", "ready"]),
  brief: datasetBriefSchema,
  message: z.string().min(1).max(600).describe("One short finding or focused question. For a sample, ask whether to build or change sources/fields."),
  limitations: z.string().max(500).describe("Material limitations only; empty if none."),
  needs_sources: z.boolean().describe("True only after an executed catalogue inspection finds missing sources."),
  query_id: z.string().nullable().describe("Executed dataset query in this turn; null when clarifying or unable to produce rows."),
  checks: z.array(z.string()).max(3).describe("Executed validation query IDs, each returning one row of boolean checks, all true to mark ready."),
})
export type DatasetBrief = z.infer<typeof datasetBriefSchema>
export type AnswerInput = z.infer<typeof answerSchema>
