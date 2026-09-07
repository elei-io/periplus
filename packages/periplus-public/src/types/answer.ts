import { z } from "zod"

const note = z.string().min(1).max(2000)

export const datasetBriefSchema = z.object({
  title: z.string().min(1).max(120),
  purpose: z.string().min(1).max(600),
  grain: z.string().min(1).max(600).describe("What one row represents; mark unresolved choices as open."),
  fields: z.string().min(1).max(600),
  population: z.string().min(1).max(600),
  time_scope: z.string().min(1).max(600),
  acceptance: z.string().min(1).max(600).describe("Required coverage, fidelity and acceptable missingness; distinguish proposals from user decisions."),
  open_questions: z.array(z.string().min(1).max(300)).max(4),
})
const confidence = z.object({ level: z.enum(["low", "medium", "high"]), reason: z.string().min(1).max(600) })

export const answerSchema = z.object({
  brief: datasetBriefSchema,
  confidence: z.object({ coverage: confidence.describe("Confidence in the coverage assessment, not the amount of data. Explain whether coverage is sufficient or insufficient."), correctness: confidence.describe("Confidence in query/measurement correctness or the stated capability assessment. Untested extraction is low confidence.") }),
  results: z.array(z.object({ query_id: z.string(), title: z.string().min(1).max(120) })).max(3),
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
