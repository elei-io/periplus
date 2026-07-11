import { useState } from "react"
import {
  Clock3Icon,
  DatabaseIcon,
  PlayIcon,
  Rows3Icon,
  SparklesIcon,
  TriangleAlertIcon,
  ViewIcon,
} from "lucide-react"

import { CatalogueResultsTable } from "@/components/catalogue/catalogue-results-table"
import { catalogueTables } from "@/components/catalogue/catalogue-schema"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { SaveViewDialog } from "@/components/catalogue/save-view-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useCatalogueQuery } from "@/hooks/use-catalogue-query"
import { useCatalogueLint } from "@/hooks/use-catalogue-lint"
import { useCatalogueViews } from "@/hooks/use-catalogue-views"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const initialSql = `SELECT *\nFROM documents\nLIMIT 100;`

const examples = [
  {
    label: "Recent documents",
    sql: `SELECT document_id, element_count, created_at\nFROM documents\nORDER BY created_at DESC\nLIMIT 100;`,
  },
  {
    label: "Links",
    sql: `WITH selected AS MATERIALIZED (\n  SELECT document_id, element_index, attributes\n  FROM elements\n  WHERE tag = 'a' AND has_attribute(attributes, 'href')\n  LIMIT 100\n)\nSELECT\n  document_id,\n  text_content(document_id, element_index) AS text,\n  get_attribute(attributes, 'href') AS href\nFROM selected;`,
  },
  {
    label: "Tag counts",
    sql: `SELECT tag, count(*) AS elements\nFROM elements\nGROUP BY tag\nORDER BY elements DESC\nLIMIT 100;`,
  },
]

export function CatalogueSqlPage() {
  const [query, setQuery] = useState(initialSql)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [saveViewOpen, setSaveViewOpen] = useState(false)
  const catalogueQuery = useCatalogueQuery()
  const catalogueLint = useCatalogueLint(query)
  const catalogueViews = useCatalogueViews()

  function execute() {
    if (!query.trim() || catalogueQuery.isPending) return
    const started = performance.now()
    setStartedAt(started)
    catalogueQuery.mutate(query, {
      onSettled: () => {
        setElapsed(performance.now() - started)
        setStartedAt(null)
      },
    })
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-y-auto">
      <section className="sql-workbench overflow-hidden rounded-xl border bg-card shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-muted/20 px-4 py-3">
          <div className="flex items-center gap-2">
            <div className="flex size-7 items-center justify-center rounded-md bg-primary/10 text-primary">
              <DatabaseIcon className="size-3.5" />
            </div>
            <div>
              <div className="text-xs font-medium">DuckLake catalogue</div>
              <div className="text-[11px] text-muted-foreground">
                Read-only · Arrow IPC
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setSaveViewOpen(true)}
                    disabled={!query.trim()}
                  />
                }
              >
                <ViewIcon /> Save as view
              </TooltipTrigger>
              <TooltipContent>
                Create a persistent, non-materialized DuckLake view from this SQL
              </TooltipContent>
            </Tooltip>
            <Badge
              variant="outline"
              className="hidden font-mono text-[10px] sm:inline-flex"
            >
              ⌘ ↵
            </Badge>
            <Button
              size="sm"
              onClick={execute}
              disabled={catalogueQuery.isPending || !query.trim()}
            >
              {catalogueQuery.isPending ? (
                <SparklesIcon className="animate-pulse" />
              ) : (
                <PlayIcon />
              )}{" "}
              {catalogueQuery.isPending ? "Running…" : "Run query"}
            </Button>
          </div>
        </div>
        <SqlEditor
          value={query}
          onChange={setQuery}
          onRun={execute}
          views={(catalogueViews.data?.items ?? []).filter(
            (view) => view.available
          )}
        />
        {catalogueLint.data && catalogueLint.data.diagnostics.length > 0 && (
          <div className="space-y-1.5 border-t border-amber-500/20 bg-amber-500/5 px-4 py-2.5">
            {catalogueLint.data.diagnostics.map((diagnostic) => (
              <div
                key={diagnostic.code}
                className="flex items-start gap-2 text-[11px] text-amber-800 dark:text-amber-300"
              >
                <TriangleAlertIcon className="mt-0.5 size-3.5 shrink-0" />
                <span>{diagnostic.message}</span>
              </div>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t bg-muted/15 px-4 py-2.5">
          <span className="mr-1 text-[11px] text-muted-foreground">Try</span>
          {examples.map((example) => (
            <Button
              key={example.label}
              variant="ghost"
              size="xs"
              className="h-6 rounded-full border bg-background px-2.5 text-[10px]"
              onClick={() => setQuery(example.sql)}
            >
              {example.label}
            </Button>
          ))}
          <span className="ml-auto hidden text-[10px] text-muted-foreground md:inline">
            Autocomplete: tables, aliases & columns
          </span>
        </div>
      </section>
      <SaveViewDialog open={saveViewOpen} onOpenChange={setSaveViewOpen} sql={query} />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
        <div className="flex min-h-5 items-center gap-3 text-[11px] text-muted-foreground">
          {catalogueQuery.data ? (
            <>
              <span className="flex items-center gap-1.5">
                <Rows3Icon className="size-3" />
                {catalogueQuery.data.rows.length.toLocaleString()} rows
              </span>
              <span>{catalogueQuery.data.columns.length} columns</span>
              {elapsed !== null && (
                <span className="flex items-center gap-1.5">
                  <Clock3Icon className="size-3" />
                  {elapsed < 1000
                    ? `${Math.round(elapsed)} ms`
                    : `${(elapsed / 1000).toFixed(2)} s`}
                </span>
              )}
            </>
          ) : (
            <span>Run a query to inspect catalogue rows.</span>
          )}
          {startedAt !== null && (
            <span className="animate-pulse">Executing query…</span>
          )}
        </div>
        {catalogueQuery.data && (
          <CatalogueResultsTable
            key={catalogueQuery.data.columns.join("\u0000")}
            result={catalogueQuery.data}
          />
        )}
        {!catalogueQuery.data && (
          <div className="grid gap-3 pt-2 lg:grid-cols-2">
            {(["documents", "elements"] as const).map((table) => (
              <button
                key={table}
                type="button"
                className="group rounded-xl border bg-card/70 p-4 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:bg-card hover:shadow-md"
                onClick={() =>
                  setQuery(`SELECT *\nFROM atlas.main.${table}\nLIMIT 100;`)
                }
              >
                <div className="mb-3 flex items-center justify-between">
                  <div className="flex items-center gap-2 font-mono text-xs font-medium">
                    <DatabaseIcon className="size-3.5 text-primary" />
                    atlas.main.{table}
                  </div>
                  <span className="text-[10px] text-muted-foreground transition-colors group-hover:text-primary">
                    Open query →
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {catalogueTables[table].map((column) => (
                    <span
                      key={column}
                      className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[9px] text-muted-foreground"
                    >
                      {column}
                    </span>
                  ))}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
