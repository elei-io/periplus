export type CatalogueQueryResult = {
  columns: string[]
  columnTypes: string[]
  rows: unknown[][]
}

export type CatalogueLintDiagnostic = {
  code: string
  severity: "warning"
  message: string
}

export type CatalogueLintResult = {
  diagnostics: CatalogueLintDiagnostic[]
}
