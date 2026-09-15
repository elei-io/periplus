export type QueryHelpers = {
  catalogue_version: string
  schema_version: "public_v1"
  relations: QueryHelpers["helpers"]
  helpers: {
    name: string
    kind: string
    description: string
    parameters: { name: string; description: string }[]
    columns: { name: string; description: string }[]
    notes: string[]
    examples: string[]
  }[]
}
