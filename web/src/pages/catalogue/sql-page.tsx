import { useState } from "react"
import CodeMirror from "@uiw/react-codemirror"
import { sql } from "@codemirror/lang-sql"
import { PlayIcon } from "lucide-react"

import { CatalogueResultsTable } from "@/components/catalogue/catalogue-results-table"
import { Button } from "@/components/ui/button"
import { useCatalogueQuery } from "@/hooks/use-catalogue-query"

const initialSql = `SELECT *\nFROM atlas.main.documents\nLIMIT 100;`

export function CatalogueSqlPage() {
  const [query, setQuery] = useState(initialSql)
  const catalogueQuery = useCatalogueQuery()

  function execute() {
    if (query.trim() && !catalogueQuery.isPending) catalogueQuery.mutate(query)
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4">
      <section className="overflow-hidden rounded-lg border bg-background shadow-sm">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <span className="text-xs font-medium text-muted-foreground">DuckDB SQL · ⌘ Enter to run</span>
          <Button size="sm" onClick={execute} disabled={catalogueQuery.isPending || !query.trim()}>
            <PlayIcon /> {catalogueQuery.isPending ? "Running…" : "Run query"}
          </Button>
        </div>
        <CodeMirror
          value={query}
          height="220px"
          extensions={[sql()]}
          onChange={setQuery}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault()
              execute()
            }
          }}
          basicSetup={{ lineNumbers: true, foldGutter: false }}
        />
      </section>
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="text-xs text-muted-foreground">
          {catalogueQuery.data ? `${catalogueQuery.data.rows.length.toLocaleString()} rows` : "Run a query to inspect catalogue rows."}
        </div>
        {catalogueQuery.data && <CatalogueResultsTable result={catalogueQuery.data} />}
      </div>
    </div>
  )
}
