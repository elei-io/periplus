import { z } from "zod"

export const datasetBriefSchema = z.object({
  title: z.string().min(1).max(120),
  grain: z.string().min(1).max(300).describe("What one row represents."),
  fields: z.array(z.object({
    name: z.string().min(1).max(128),
    type: z.string().min(1).max(60).describe("Exact executed DuckDB type."),
    meaning: z.string().max(160),
    nullable: z.boolean(),
  })).min(1).max(64),
  population: z.string().min(1).max(500).describe("Included sources, coverage and observation selection; unresolved until inspected."),
})

export const datasetDraftSchema = datasetBriefSchema.extend({
  title: z.string().max(120),
  grain: z.string().max(300),
  population: z.string().max(500),
  fields: z.array(datasetBriefSchema.shape.fields.element.extend({
    name: z.string().max(128),
    type: z.string().max(60),
  })).max(64),
})

export const datasetSuggestionSchema = z.object({
  query_id: z.string().min(1),
  title: z.string().min(1).max(120),
  grain: z.string().min(1).max(300),
  population: z.string().min(1).max(500),
  limitations: z.string().max(500),
  checks: z.array(z.string()).max(8).describe("SQL query IDs for semantic validation: each check returns one row of BOOLEAN columns, true means passed. Empty for a preview."),
})
export const coverageSuggestionSchema = z.object({
  description: z.string().min(1).max(2000).describe("Sources or topics to collect, suitable for the coverage request form."),
  reason: z.string().min(1).max(500),
})
export type DatasetBrief = z.infer<typeof datasetBriefSchema>
export type DatasetSuggestion = z.infer<typeof datasetSuggestionSchema>
export type CoverageSuggestion = z.infer<typeof coverageSuggestionSchema>

export type DatasetMode = "discover" | "build"
export const datasetRequestSchema = z.object({
  mode: z.enum(["discover", "build"]),
  contract: datasetBriefSchema.optional(),
}).superRefine((value, context) => {
  if (value.mode === "discover" && value.contract) context.addIssue({ code: "custom", message: "Discovery does not accept a required schema." })
  if (value.contract && new Set(value.contract.fields.map(field => field.name)).size !== value.contract.fields.length) context.addIssue({ code: "custom", message: "Column names must be unique." })
})

export function sameDatasetBrief(left: unknown, right: unknown): boolean {
  const a = datasetBriefSchema.safeParse(left)
  const b = datasetBriefSchema.safeParse(right)
  return a.success && b.success && JSON.stringify(a.data) === JSON.stringify(b.data)
}
