import { z } from "zod"

const note = z.string().min(1).max(2000)

export const datasetBriefSchema = z.object({
  title: z.string().min(1).max(120),
  purpose: z.string().min(1).max(600),
  grain: z.string().min(1).max(600).describe("What one row represents; mark unresolved choices as open."),
  fields: z.array(z.object({
    name: z.string().regex(/^[a-z_][a-z0-9_]*$/).max(60),
    type: z.string().min(1).max(60).describe("Exact DuckDB output type, e.g. VARCHAR, BIGINT, DECIMAL(10,2)."),
    meaning: z.string().min(1).max(240),
    nullable: z.boolean().describe("Whether missing values are acceptable in this field."),
  })).min(1).max(16),
  population: z.string().min(1).max(600),
  time_scope: z.string().min(1).max(600),
  acceptance: z.string().min(1).max(600).describe("Required coverage, fidelity and acceptable missingness; distinguish proposals from user decisions."),
  open_questions: z.array(z.string().min(1).max(300)).max(4),
})
const confidence = z.object({ level: z.enum(["low", "medium", "high"]), reason: z.string().min(1).max(600) })

export const answerSchema = z.object({
  brief: datasetBriefSchema,
  source_plan: z.object({
    material: z.string().min(1).max(800).describe("What the catalogue actually contains, including relevant page types and coverage."),
    approach: z.string().min(1).max(1000).describe("How observed page structures can supply the requested records and fields, separate from output schema."),
    query_ids: z.array(z.string()).min(1).max(4).describe("Successful queries from this turn supporting the material and extraction approach."),
  }).nullable().describe("Null until source material has been inspected successfully; never invent a source or currency."),
  confidence: z.object({ coverage: confidence.describe("Confidence in the coverage assessment, not the amount of data. Explain whether coverage is sufficient or insufficient."), correctness: confidence.describe("Confidence in query/measurement correctness or the stated capability assessment. Untested extraction is low confidence.") }),
  results: z.array(z.object({ query_id: z.string(), title: z.string().min(1).max(120) })).max(3),
  dataset_query_id: z.string().nullable().describe("The selected executed query that produces the user's schema, not a coverage or validation query. Null until constructed."),
  validation: z.array(z.object({
    check: z.string().min(1).max(160),
    status: z.enum(["passed", "failed", "untested"]),
    detail: z.string().min(1).max(600),
    query_ids: z.array(z.string()).max(4).describe("Executed query IDs supporting this check; not necessarily selected display results."),
  })).max(8),
  context: z.string().min(1).max(800),
  analysis: z.array(z.object({
    text: note.describe("Agent interpretation, summary or generated label; never quoted source content."),
    evidence_query_ids: z.array(z.string()).min(1).max(3),
  })).max(24),
  outcome: z.object({
    status: z.enum(["designing", "ready", "collection_needed", "not_fit", "blocked"]).describe("Designing is ongoing, blocked is operational. Terminal outcomes: ready, collection_needed, not_fit. Judge the task, never the user."),
    requested_information: note.describe("Write complete concise sentences. Check the requested fields and grain against the actual output; name anything missing."),
    source_fidelity: note.describe("State what is preserved, SQL-transformed or agent-generated, including text truncation."),
    limitations: note.describe("State missing values, exclusions, sample and display limits, or explicitly say none were identified."),
    next_step: note.describe("Can the user take their intended next step? Say how, or state the remaining blocker."),
  }),
})

export type AnswerInput = z.infer<typeof answerSchema>

export type DatasetBrief = z.infer<typeof datasetBriefSchema>
