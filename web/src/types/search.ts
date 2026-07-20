export type SearchResultType = "coverage" | "pages" | "passages"

export type SearchTypeDefinition = {
  type: SearchResultType
  label: string
  description: string
}

export type SearchTypeRegistry = {
  items: SearchTypeDefinition[]
}

export type SearchCompilation = {
  result_type: SearchResultType
  sql: string
  investigation_sql: string
}
