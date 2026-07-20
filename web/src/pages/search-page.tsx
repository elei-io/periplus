import { useEffect, useMemo, useState } from "react"
import { CopyIcon, SearchIcon } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { useCatalogueQuery } from "@/hooks/use-catalogue-query"
import { compileSearch, useSearchTypes } from "@/hooks/use-search"
import { extractApiError } from "@/lib/api"
import type { CatalogueQueryResult } from "@/types/catalogue"
import type { SearchCompilation, SearchResultType } from "@/types/search"

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "—"
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

function resultUrl(column: string, value: unknown) {
  if (column !== "page_url" || typeof value !== "string") return null
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : null
  } catch {
    return null
  }
}

function SearchResults({ result }: { result: CatalogueQueryResult }) {
  if (result.rows.length === 0) {
    return (
      <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        No retained evidence matched this search.
      </div>
    )
  }

  return (
    <Table containerClassName="max-h-[34rem] rounded-lg border">
      <TableHeader className="sticky top-0 z-10 bg-background">
        <TableRow>
          {result.columns.map((column) => (
            <TableHead key={column}>{column.replaceAll("_", " ")}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {result.rows.map((row, rowIndex) => (
          <TableRow key={rowIndex}>
            {row.map((value, columnIndex) => {
              const column = result.columns[columnIndex]
              const url = resultUrl(column, value)
              return (
                <TableCell
                  key={`${rowIndex}-${column}`}
                  className={
                    column === "passage" || column === "description"
                      ? "max-w-md whitespace-normal"
                      : "max-w-xs overflow-hidden text-ellipsis"
                  }
                >
                  {url ? (
                    <a
                      className="text-primary underline-offset-4 hover:underline"
                      href={url}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {displayValue(value)}
                    </a>
                  ) : (
                    displayValue(value)
                  )}
                </TableCell>
              )
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function SearchPage() {
  const registry = useSearchTypes()
  const catalogueQuery = useCatalogueQuery()
  const [query, setQuery] = useState("")
  const [resultType, setResultType] = useState<SearchResultType>("coverage")
  const [compilation, setCompilation] = useState<SearchCompilation | null>(null)
  const [result, setResult] = useState<CatalogueQueryResult | null>(null)

  useEffect(() => {
    const items = registry.data?.items
    const first = items?.[0]
    if (first && !items.some((item) => item.type === resultType)) {
      setResultType(first.type)
    }
  }, [registry.data, resultType])

  const selectedType = useMemo(
    () => registry.data?.items.find((item) => item.type === resultType),
    [registry.data, resultType]
  )

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    const normalizedQuery = query.trim()
    if (!normalizedQuery) return

    let nextCompilation: SearchCompilation
    try {
      nextCompilation = await compileSearch(normalizedQuery, resultType)
    } catch (error) {
      toast.error(extractApiError(error))
      return
    }

    setResult(null)
    setCompilation(nextCompilation)
    try {
      setResult(await catalogueQuery.mutateAsync({ sql: nextCompilation.sql }))
    } catch {
      // useCatalogueQuery surfaces execution failures through toast.error.
    }
  }

  async function copySql() {
    if (!compilation) return
    try {
      await navigator.clipboard.writeText(compilation.investigation_sql)
      toast.success("Investigation SQL copied.")
    } catch (error) {
      toast.error(extractApiError(error))
    }
  }

  const isRunning = catalogueQuery.isPending

  return (
    <main className="mx-auto flex w-full max-w-7xl flex-col gap-6">
      <section className="flex min-h-48 flex-col justify-center gap-3">
        <div>
          <p className="text-xs font-medium tracking-[0.18em] text-primary uppercase">
            Explore retained evidence
          </p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">
            What did Atlas find?
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            Search the corpus by coverage, matching pages, or exact passages,
            then continue the investigation in SQL.
          </p>
        </div>

        <form
          className="mt-3 flex flex-col gap-2 sm:flex-row"
          onSubmit={handleSubmit}
        >
          <div className="relative min-w-0 flex-1">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              autoFocus
              className="h-11 pl-10"
              onChange={(event) => setQuery(event.target.value)}
              placeholder="A topic, phrase, company, product…"
              value={query}
            />
          </div>
          <Select
            onValueChange={(value) => setResultType(value as SearchResultType)}
            value={resultType}
          >
            <SelectTrigger className="h-11 w-full sm:w-44">
              {selectedType?.label ?? "Result type"}
            </SelectTrigger>
            <SelectContent>
              {registry.data?.items.map((item) => (
                <SelectItem key={item.type} value={item.type}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            className="h-11 px-6"
            disabled={isRunning || !query.trim()}
            type="submit"
          >
            {isRunning ? "Searching…" : "Search"}
          </Button>
        </form>
        {selectedType && (
          <p className="text-xs text-muted-foreground">
            {selectedType.description}
          </p>
        )}
      </section>

      {(result || isRunning) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              {selectedType?.label ?? "Search"} results
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isRunning ? (
              <div
                className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground"
                role="status"
              >
                Searching the catalogue…
              </div>
            ) : (
              result && <SearchResults result={result} />
            )}
          </CardContent>
        </Card>
      )}

      {compilation && (
        <details className="rounded-xl border bg-card">
          <summary className="cursor-pointer px-5 py-4 text-sm font-medium">
            Investigate with SQL
          </summary>
          <div className="space-y-3 border-t p-5">
            <p className="text-xs text-muted-foreground">
              This is the deterministic catalogue query used for the result.
              Edit or save it in the Catalogue Workbench for deeper analysis.
            </p>
            <Textarea
              aria-label="Investigation SQL"
              className="min-h-64 font-mono text-xs"
              readOnly
              value={compilation.investigation_sql}
            />
            <Button onClick={copySql} size="sm" variant="outline">
              <CopyIcon />
              Copy SQL
            </Button>
          </div>
        </details>
      )}
    </main>
  )
}
