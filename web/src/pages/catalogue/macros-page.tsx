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
import { SaveScalarMacroDialog } from "@/components/catalogue/save-scalar-macro-dialog"
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
import {
  useCatalogueScalarMacros,
  useDropCatalogueScalarMacro,
  useUpdateCatalogueScalarMacro,
} from "@/hooks/use-catalogue-scalar-macros"
import type {
  CatalogueMacroRecord,
  CatalogueScalarMacroRecord,
  CatalogueTableMacroRecord,
} from "@/types/catalogue"

export function CatalogueMacrosPage({ macroId }: { macroId?: string }) {
  const [createTableOpen, setCreateTableOpen] = useState(false)
  const [createScalarOpen, setCreateScalarOpen] = useState(false)
  const tableMacrosQuery = useCatalogueTableMacros()
  const scalarMacrosQuery = useCatalogueScalarMacros()
  const macros: CatalogueMacroRecord[] = [
    ...(scalarMacrosQuery.data?.items ?? []),
    ...(tableMacrosQuery.data?.items ?? []),
  ].sort((left, right) => left.slug.localeCompare(right.slug))
  const loading = tableMacrosQuery.isLoading || scalarMacrosQuery.isLoading
  const selected = macroId ? macros.find((macro) => macro.id === macroId) : null

  if (macroId) {
    if (!selected) {
      return (
        <CataloguePanel>
          <CatalogueEmptyState
            icon={BracesIcon}
            title={
              loading ? "Loading macro…" : "Macro not found"
            }
            description={
              loading
                ? "Fetching its definition and parameters."
                : "It may have been deleted."
            }
            action={
              !loading ? (
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
        {selected.kind === "scalar" ? (
          <ScalarMacroDetail
            key={selected.definition_revision_id}
            macro={selected}
          />
        ) : (
          <TableMacroDetail
            key={selected.definition_revision_id}
            macro={selected}
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <CatalogueHero
        icon={BracesIcon}
        eyebrow="Reusable catalogue SQL"
        title="Macros"
        description="Manage reusable scalar expressions and parameterized table relations in one namespace."
      >
        <Badge variant="outline" className="h-7 bg-background/40 px-3">
          {macros.length} macros
        </Badge>
        <Button
          variant="outline"
          onClick={() => setCreateScalarOpen(true)}
          className="rounded-full px-4"
        >
          <SparklesIcon /> Create scalar
        </Button>
        <Button
          onClick={() => setCreateTableOpen(true)}
          className="rounded-full px-4"
        >
          <SparklesIcon /> Create table macro
        </Button>
      </CatalogueHero>
      <div className="flex justify-end">
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            void tableMacrosQuery.refetch()
            void scalarMacrosQuery.refetch()
          }}
          disabled={
            tableMacrosQuery.isFetching || scalarMacrosQuery.isFetching
          }
        >
          <RefreshCwIcon
            className={
              tableMacrosQuery.isFetching || scalarMacrosQuery.isFetching
                ? "animate-spin"
                : ""
            }
          />
          Refresh
        </Button>
      </div>
      {macros.length > 0 ? (
        <CataloguePanel>
          <Table>
            <TableHeader className="bg-muted/20">
              <TableRow>
                <TableHead className="pl-4">Macro</TableHead>
                <TableHead>Kind</TableHead>
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
                              <Badge className="ml-1">System</Badge>
                            ) : null}
                          </span>
                          <span className="block truncate font-mono text-[10px] text-muted-foreground">
                            {macro.qualified_name}
                          </span>
                        </span>
                      </a>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">
                        {macro.kind === "scalar" ? "Scalar" : "Table"}
                      </Badge>
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
      ) : !loading ? (
        <CataloguePanel>
          <CatalogueEmptyState
            icon={BracesIcon}
            title="No macros yet"
            description="Create a reusable scalar expression or parameterized relation."
            action={
              <Button onClick={() => setCreateScalarOpen(true)}>
                Create scalar macro
              </Button>
            }
            className="min-h-[28rem]"
          />
        </CataloguePanel>
      ) : null}
      <SaveTableMacroDialog
        open={createTableOpen}
        onOpenChange={setCreateTableOpen}
        sql={"SELECT *\nFROM documents\nLIMIT 100;"}
      />
      <SaveScalarMacroDialog
        open={createScalarOpen}
        onOpenChange={setCreateScalarOpen}
      />
    </div>
  )
}

function TableMacroDetail({ macro }: { macro: CatalogueTableMacroRecord }) {
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
            {macro.fixture_path ? <Badge>System</Badge> : null}
            {dirty ? <Badge variant="outline">Unsaved</Badge> : null}
          </div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">
            {macro.qualified_name}
          </div>
        </div>
        <Button
          size="sm"
          variant="destructive"
          disabled={drop.isPending || Boolean(macro.fixture_path)}
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
              disabled={Boolean(macro.fixture_path)}
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
              disabled={Boolean(macro.fixture_path)}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this macro returns"
            />
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label>Parameters</Label>
          <Input
            disabled={Boolean(macro.fixture_path)}
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
            onChange={macro.fixture_path ? undefined : setSql}
            readOnly={Boolean(macro.fixture_path)}
            height="360px"
            ariaLabel={`SQL definition for ${macro.qualified_name}`}
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
            disabled={
              Boolean(macro.fixture_path) ||
              !dirty ||
              !sql.trim() ||
              update.isPending
            }
          >
            {update.isPending ? "Saving…" : "Save changes"}
          </Button>
        </div>
      </div>
    </CataloguePanel>
  )
}

function ScalarMacroDetail({ macro }: { macro: CatalogueScalarMacroRecord }) {
  const [sql, setSql] = useState(macro.sql)
  const [slug, setSlug] = useState(macro.slug)
  const [description, setDescription] = useState(macro.description ?? "")
  const [parameters, setParameters] = useState(macro.parameters.join(", "))
  const update = useUpdateCatalogueScalarMacro()
  const drop = useDropCatalogueScalarMacro()
  const system = Boolean(macro.fixture_path)
  const parsedParameters = parseParameters(parameters)
  const dirty =
    sql !== macro.sql ||
    slug !== macro.slug ||
    description !== (macro.description ?? "") ||
    parameters !== macro.parameters.join(", ")
  const usage = `SELECT ${macro.qualified_name}(${parsedParameters
    .map((parameter) => parameter)
    .join(", ")});`

  return (
    <CataloguePanel className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b bg-gradient-to-r from-primary/5 to-transparent px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">{macro.slug}</h2>
            <Badge variant="outline">Scalar</Badge>
            <Badge variant={macro.available ? "secondary" : "destructive"}>
              {macro.available ? "Available" : "Missing"}
            </Badge>
            {system ? <Badge>System</Badge> : null}
            {dirty ? <Badge variant="outline">Unsaved</Badge> : null}
          </div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">
            {macro.qualified_name}
          </div>
        </div>
        <Button
          size="sm"
          variant="destructive"
          disabled={system || drop.isPending}
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
              disabled={system}
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
              disabled={system}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label>Parameters</Label>
          <Input
            value={parameters}
            disabled={system}
            onChange={(event) => setParameters(event.target.value)}
          />
        </div>
        <div className="overflow-hidden rounded-2xl border bg-[#0d1117] shadow-inner">
          <div className="border-b border-white/10 px-4 py-2 text-[10px] text-white/45">
            SCALAR EXPRESSION
          </div>
          <SqlEditor
            value={sql}
            onChange={system ? undefined : setSql}
            readOnly={system}
            height="360px"
            ariaLabel={`SQL definition for ${macro.qualified_name}`}
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
            disabled={system || !dirty || !sql.trim() || update.isPending}
            onClick={() =>
              update.mutate({
                macro,
                sql,
                slug,
                description,
                parameters: parsedParameters,
              })
            }
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
