import { useState } from "react"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BracesIcon,
  RefreshCwIcon,
  SparklesIcon,
  Trash2Icon,
} from "lucide-react"

import {
  CatalogueEmptyState,
  CatalogueHero,
  CataloguePanel,
} from "@/components/catalogue/catalogue-workspace"
import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { SaveTableMacroDialog } from "@/components/catalogue/save-table-macro-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useCatalogueTableMacros,
  useDropCatalogueTableMacro,
  useUpdateCatalogueTableMacro,
} from "@/hooks/use-catalogue-table-macros"
import type { CatalogueTableMacroRecord } from "@/types/catalogue"

export function CatalogueTableMacrosPage({ macroId }: { macroId?: string }) {
  const [createOpen, setCreateOpen] = useState(false)
  const macrosQuery = useCatalogueTableMacros()
  const macros = macrosQuery.data?.items ?? []
  const selected = macroId ? macros.find((macro) => macro.id === macroId) : null

  if (macroId) {
    if (!selected) {
      return (
        <CataloguePanel>
          <CatalogueEmptyState
            icon={BracesIcon}
            title={
              macrosQuery.isLoading
                ? "Loading table macro…"
                : "Table macro not found"
            }
            description={
              macrosQuery.isLoading
                ? "Fetching its definition and parameters."
                : "It may have been deleted."
            }
            action={
              !macrosQuery.isLoading ? (
                <Button
                  nativeButton={false}
                  render={<a href="/catalogue/macros" />}
                >
                  Back to macros
                </Button>
              ) : undefined
            }
            className="min-h-[32rem]"
          />
        </CataloguePanel>
      )
    }
    return (
      <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
        <div>
          <Button
            nativeButton={false}
            render={<a href="/catalogue/macros" />}
            variant="ghost"
          >
            <ArrowLeftIcon /> All macros
          </Button>
        </div>
        <MacroDetail key={selected.definition_revision_id} macro={selected} />
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <CatalogueHero
        icon={BracesIcon}
        eyebrow="Reusable catalogue SQL"
        title="Table macros"
        description="Create parameterized relations that expand into the calling query without storing result rows."
      >
        <Badge variant="outline" className="h-7 bg-background/40 px-3">
          {macros.length} macros
        </Badge>
        <Button
          onClick={() => setCreateOpen(true)}
          className="rounded-full px-4"
        >
          <SparklesIcon /> Create macro
        </Button>
      </CatalogueHero>
      <div className="flex justify-end">
        <Button
          variant="outline"
          size="sm"
          onClick={() => void macrosQuery.refetch()}
          disabled={macrosQuery.isFetching}
        >
          <RefreshCwIcon
            className={macrosQuery.isFetching ? "animate-spin" : ""}
          />
          Refresh
        </Button>
      </div>
      {macros.length > 0 ? (
        <CataloguePanel>
          <Table>
            <TableHeader className="bg-muted/20">
              <TableRow>
                <TableHead className="pl-4">Table macro</TableHead>
                <TableHead>Parameters</TableHead>
                <TableHead>Availability</TableHead>
                <TableHead>Last update</TableHead>
                <TableHead className="w-20" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {macros.map((macro) => {
                const href = `/catalogue/macros/${macro.id}`
                return (
                  <TableRow key={macro.id} className="group">
                    <TableCell className="max-w-sm py-3 pl-4">
                      <a
                        href={href}
                        className="flex min-w-0 items-center gap-3"
                      >
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                          <BracesIcon className="size-3.5" />
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate font-medium group-hover:text-primary">
                            {macro.slug}{" "}
                            {macro.fixture_path ? (
                              <Badge className="ml-1">Fixture</Badge>
                            ) : null}
                          </span>
                          <span className="block truncate font-mono text-[10px] text-muted-foreground">
                            {macro.qualified_name}
                          </span>
                        </span>
                      </a>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs text-muted-foreground">
                        ({macro.parameters.join(", ")})
                      </code>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={macro.available ? "outline" : "destructive"}
                        className={
                          macro.available
                            ? "border-emerald-500/25 text-emerald-500"
                            : undefined
                        }
                      >
                        {macro.available ? "Available" : "Missing"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {new Date(macro.updated_at).toLocaleString()}
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <Button
                        nativeButton={false}
                        render={<a href={href} />}
                        size="sm"
                        variant="ghost"
                      >
                        Open <ArrowRightIcon />
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CataloguePanel>
      ) : !macrosQuery.isLoading ? (
        <CataloguePanel>
          <CatalogueEmptyState
            icon={BracesIcon}
            title="No table macros yet"
            description="Turn useful SQL into a reusable, parameterized relation."
            action={
              <Button onClick={() => setCreateOpen(true)}>Create macro</Button>
            }
            className="min-h-[28rem]"
          />
        </CataloguePanel>
      ) : null}
      <SaveTableMacroDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        sql={"SELECT *\nFROM documents\nLIMIT 100;"}
      />
    </div>
  )
}

function MacroDetail({ macro }: { macro: CatalogueTableMacroRecord }) {
  const [sql, setSql] = useState(() => formatSql(macro.sql))
  const [slug, setSlug] = useState(macro.slug)
  const [description, setDescription] = useState(macro.description ?? "")
  const [parameters, setParameters] = useState(macro.parameters.join(", "))
  const update = useUpdateCatalogueTableMacro()
  const drop = useDropCatalogueTableMacro()
  const parsedParameters = parseParameters(parameters)
  const dirty =
    sql !== formatSql(macro.sql) ||
    slug !== macro.slug ||
    description !== (macro.description ?? "") ||
    parameters !== macro.parameters.join(", ")

  function save() {
    update.mutate({
      macro,
      sql,
      slug,
      description,
      parameters: parsedParameters,
    })
  }

  const usage = `SELECT *\nFROM ${macro.qualified_name}(${parsedParameters
    .map((parameter) => `'${parameter}'`)
    .join(", ")});`

  return (
    <CataloguePanel className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b bg-gradient-to-r from-primary/5 to-transparent px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">{macro.slug}</h2>
            <Badge variant={macro.available ? "secondary" : "destructive"}>
              {macro.available ? "Available" : "Missing"}
            </Badge>
            {macro.fixture_path ? <Badge>Fixture</Badge> : null}
            {dirty ? <Badge variant="outline">Unsaved</Badge> : null}
          </div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">
            {macro.qualified_name}
          </div>
        </div>
        <Button
          size="sm"
          variant="destructive"
          disabled={drop.isPending}
          onClick={() => {
            if (window.confirm(`Delete ${macro.qualified_name}?`)) {
              drop.mutate(macro, {
                onSuccess: () => {
                  window.history.pushState(null, "", "/catalogue/macros")
                  window.dispatchEvent(new PopStateEvent("popstate"))
                },
              })
            }
          }}
        >
          <Trash2Icon /> Delete
        </Button>
      </div>
      <div className="grid gap-4 p-5">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="grid gap-1.5">
            <Label>Display name</Label>
            <Input
              value={slug}
              onChange={(event) =>
                setSlug(
                  event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-")
                )
              }
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Description</Label>
            <Input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this macro returns"
            />
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label>Parameters</Label>
          <Input
            value={parameters}
            onChange={(event) => setParameters(event.target.value)}
            placeholder="hostname, path_pattern"
          />
        </div>
        <div className="overflow-hidden rounded-2xl border bg-[#0d1117] shadow-inner">
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-[10px] text-white/45">
            <span>TABLE MACRO QUERY</span>
            <Button
              size="xs"
              variant="ghost"
              className="text-white/60 hover:bg-white/10 hover:text-white"
              onClick={() => setSql(formatSql(sql))}
            >
              <SparklesIcon /> Format SQL
            </Button>
          </div>
          <SqlEditor
            value={sql}
            onChange={setSql}
            height="360px"
            ariaLabel={`SQL definition for ${macro.qualified_name}`}
            enableCssSelect
          />
        </div>
        <div className="grid gap-1.5">
          <Label>Usage</Label>
          <pre className="overflow-x-auto rounded-xl border bg-muted/20 p-3 font-mono text-xs">
            {usage}
          </pre>
        </div>
        <div className="flex justify-end">
          <Button
            onClick={save}
            disabled={!dirty || !sql.trim() || update.isPending}
          >
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>
    </CataloguePanel>
  )
}

function parseParameters(value: string): string[] {
  return value
    .split(",")
    .map((parameter) => parameter.trim())
    .filter(Boolean)
}
