export type QueryHelpers = {
  catalogue_version: string
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
