import { useState } from "react"
import { toast } from "sonner"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  BracesIcon,
  Columns3Icon,
  DatabaseIcon,
  DatabaseZapIcon,
  RefreshCwIcon,
  SparklesIcon,
  Trash2Icon,
  UnlinkIcon,
} from "lucide-react"

import { MaterializeViewDialog } from "@/components/catalogue/materialize-view-dialog"
import { MaterializationSheet } from "@/components/catalogue/materialization-sheet"
import { SaveViewDialog } from "@/components/catalogue/save-view-dialog"
import {
  CatalogueEmptyState,
  CatalogueHero,
  CataloguePanel,
} from "@/components/catalogue/catalogue-workspace"
import { CompilerAdvisory } from "@/components/catalogue/compiler-advisory"
import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
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
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useAdoptCatalogueView,
  useCatalogueViews,
  useDetachCatalogueView,
  useDropCatalogueView,
  useUpdateCatalogueView,
} from "@/hooks/use-catalogue-views"
import { useCatalogueMaterialization } from "@/hooks/use-catalogue-materializations"
import type { CatalogueViewRecord } from "@/types/catalogue"

export function CatalogueViewsPage({ viewId }: { viewId?: string }) {
  const [createOpen, setCreateOpen] = useState(false)
  const viewsQuery = useCatalogueViews()
  const views = viewsQuery.data?.items ?? []
  const managed = views.filter((view) => view.managed).length
  const selected = viewId
    ? views.find(
        (view) => view.id === viewId || view.ducklake_view_uuid === viewId
      )
    : null

  if (viewId) {
    if (!selected) {
      return (
        <CataloguePanel>
          <CatalogueEmptyState
            icon={BracesIcon}
            title={viewsQuery.isLoading ? "Loading view…" : "View not found"}
            description={
              viewsQuery.isLoading
                ? "Fetching its DuckLake definition and Atlas metadata."
                : "The view may have been replaced or removed."
            }
            action={
              !viewsQuery.isLoading ? (
                <Button
                  nativeButton={false}
                  render={<a href="/catalogue/views" />}
                >
                  Back to views
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
            render={<a href="/catalogue/views" />}
            variant="ghost"
          >
            <ArrowLeftIcon />
            All views
          </Button>
        </div>
        <ViewDetail key={selected.ducklake_view_uuid} view={selected} />
      </div>
    )
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <CatalogueHero
        icon={BracesIcon}
        eyebrow="Live catalogue layer"
        title="Views"
        description="Browse named DuckLake relations, inspect their schema, edit their SQL, or enable live materialization."
      >
        <Badge variant="outline" className="h-7 bg-background/40 px-3">
          {views.length} views
        </Badge>
        <Badge variant="outline" className="h-7 bg-background/40 px-3">
          {managed} managed
        </Badge>
        <Button
          onClick={() => setCreateOpen(true)}
          className="rounded-full px-4"
        >
          <SparklesIcon />
          Create view
        </Button>
      </CatalogueHero>
      <div className="flex justify-end">
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                variant="outline"
                size="sm"
                aria-label="Refresh catalogue views"
                onClick={() => void viewsQuery.refetch()}
                disabled={viewsQuery.isFetching}
              />
            }
          >
            <RefreshCwIcon
              className={viewsQuery.isFetching ? "animate-spin" : ""}
            />
            Refresh catalogue
          </TooltipTrigger>
          <TooltipContent>
            Refresh DuckLake views and Atlas references
          </TooltipContent>
        </Tooltip>
      </div>
      {views.length > 0 ? (
        <CataloguePanel>
          <Table>
            <TableHeader className="bg-muted/20">
              <TableRow>
                <TableHead className="pl-4">View</TableHead>
                <TableHead>Ownership</TableHead>
                <TableHead className="text-right">Columns</TableHead>
                <TableHead>Materialization</TableHead>
                <TableHead>Availability</TableHead>
                <TableHead className="w-20" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {views.map((view) => {
                const href = `/catalogue/views/${view.id ?? view.ducklake_view_uuid}`
                return (
                  <TableRow key={view.ducklake_view_uuid} className="group">
                    <TableCell className="max-w-sm py-3 pl-4">
                      <a
                        href={href}
                        className="flex min-w-0 items-center gap-3 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                      >
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                          <BracesIcon className="size-3.5" />
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate font-medium group-hover:text-primary">
                            {view.slug}
                          </span>
                          <span className="block truncate font-mono text-[10px] text-muted-foreground">
                            {view.qualified_name}
                          </span>
                        </span>
                      </a>
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          view.fixture_path
                            ? "default"
                            : view.managed
                              ? "secondary"
                              : "outline"
                        }
                      >
                        {view.fixture_path
                          ? "Fixture"
                          : view.managed
                            ? "Managed"
                            : "Unowned"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono tabular-nums">
                      {view.columns.length}
                    </TableCell>
                    <TableCell>
                      <MaterializationCell
                        materialization={view.materialization}
                      />
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={view.available ? "outline" : "destructive"}
                        className={
                          view.available
                            ? "border-emerald-500/25 text-emerald-500"
                            : undefined
                        }
                      >
                        {view.available ? "Available" : "Missing"}
                      </Badge>
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <Button
                        nativeButton={false}
                        render={<a href={href} />}
                        size="sm"
                        variant="ghost"
                      >
                        Open
                        <ArrowRightIcon />
                      </Button>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CataloguePanel>
      ) : (
        !viewsQuery.isLoading && (
          <CataloguePanel>
            <CatalogueEmptyState
              icon={BracesIcon}
              title="No views yet"
              description="Create a lightweight, reusable DuckLake view."
              action={
                <Button onClick={() => setCreateOpen(true)}>Create view</Button>
              }
              className="min-h-[28rem]"
            />
          </CataloguePanel>
        )
      )}
      <SaveViewDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        sql={"SELECT *\nFROM documents\nLIMIT 100;"}
      />
    </div>
  )
}

function ViewDetail({ view }: { view: CatalogueViewRecord }) {
  const [sql, setSql] = useState(() => formatSql(view.sql))
  const [slug, setSlug] = useState(view.slug)
  const [description, setDescription] = useState(view.description ?? "")
  const update = useUpdateCatalogueView()
  const adopt = useAdoptCatalogueView()
  const detach = useDetachCatalogueView()
  const drop = useDropCatalogueView()
  const [materializeOpen, setMaterializeOpen] = useState(false)
  const materialization = view.materialization
  const materializationQuery = useCatalogueMaterialization(materialization?.id)
  const dirty =
    sql !== formatSql(view.sql) ||
    slug !== view.slug ||
    description !== (view.description ?? "")

  const save = () => {
    if (sql.trim() !== view.sql.trim() && materialization) {
      toast.error("Dematerialize this view before changing its SQL definition.")
      return
    }
    update.mutate({ view, sql, slug, description })
  }

  return (
    <>
      <CataloguePanel className="flex min-h-0 flex-col">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b bg-gradient-to-r from-primary/5 to-transparent px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <DatabaseIcon className="size-4" />
            </span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate text-base font-semibold">
                  {view.slug}
                </h2>
                <ViewStatus view={view} />
                {dirty && (
                  <Badge
                    variant="outline"
                    className="border-amber-500/30 text-amber-500"
                  >
                    Unsaved
                  </Badge>
                )}
              </div>
              <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
                {view.qualified_name} · {view.ducklake_view_uuid}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {materialization && (
              <MaterializationSheet
                materialization={materializationQuery.data}
                status={materialization.status}
              />
            )}
            {view.available && view.managed && !materialization && (
              <Button size="sm" onClick={() => setMaterializeOpen(true)}>
                <DatabaseZapIcon />
                Materialize
              </Button>
            )}
            {!view.managed && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => adopt.mutate(view)}
                disabled={adopt.isPending}
              >
                Adopt view
              </Button>
            )}
            {view.managed && (
              <Tooltip>
                <TooltipTrigger render={<span className="inline-flex" />}>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => detach.mutate(view)}
                    disabled={Boolean(materialization) || detach.isPending}
                  >
                    <UnlinkIcon />
                    Detach
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {materialization
                    ? "Dematerialize this view before detaching its Atlas reference."
                    : "Remove only the Atlas reference; keep the DuckLake view."}
                </TooltipContent>
              </Tooltip>
            )}
            {view.managed && (
              <Tooltip>
                <TooltipTrigger render={<span className="inline-flex" />}>
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => {
                      if (
                        window.confirm(
                          `Permanently drop ${view.qualified_name} from DuckLake? Its Atlas reference will also be archived.`
                        )
                      )
                        drop.mutate(view)
                    }}
                    disabled={Boolean(materialization) || drop.isPending}
                  >
                    <Trash2Icon />
                    Drop
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  {materialization
                    ? "Dematerialize this view before deleting its DuckLake object."
                    : "Delete the DuckLake object and archive its Atlas reference."}
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="grid gap-4">
            <CompilerAdvisory
              outcome={view.compiler_outcome}
              diagnostics={view.compiler_diagnostics}
              dependencies={view.compiler_dependencies}
              compilerVersion={view.compiler_version}
              catalogueRevision={view.catalogue_definition_revision}
            />
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
              <div className="grid gap-1.5">
                <Label>Slug</Label>
                <Input
                  value={slug}
                  onChange={(event) =>
                    setSlug(
                      event.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9_-]/g, "-")
                    )
                  }
                  disabled={!view.managed}
                />
              </div>
              <div className="grid gap-1.5">
                <Label>Description</Label>
                <Input
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  disabled={!view.managed}
                  placeholder="What is this view useful for?"
                />
              </div>
            </div>

            <div className="overflow-hidden rounded-2xl border bg-[#0d1117] shadow-inner">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-2 text-[10px] text-white/45">
                <span>VIEW DEFINITION</span>
                <div className="flex items-center gap-2">
                  <span>{view.columns.length} output columns</span>
                  {view.managed && view.available && (
                    <Button
                      size="xs"
                      variant="ghost"
                      className="text-white/60 hover:bg-white/10 hover:text-white"
                      onClick={() => setSql(formatSql(sql))}
                    >
                      <SparklesIcon />
                      Format SQL
                    </Button>
                  )}
                </div>
              </div>
              <SqlEditor
                value={sql}
                onChange={setSql}
                readOnly={!view.managed || !view.available}
                height="320px"
                ariaLabel={`SQL definition for ${view.qualified_name}`}
              />
            </div>

            <div className="rounded-2xl border">
              <div className="flex items-center justify-between border-b px-4 py-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Columns3Icon className="size-4 text-muted-foreground" />
                  Output columns
                </div>
                <span className="text-xs text-muted-foreground">
                  {view.columns.length} columns
                </span>
              </div>
              {view.columns.length > 0 ? (
                <div className="grid sm:grid-cols-2 xl:grid-cols-3">
                  {view.columns.map((column, index) => (
                    <div
                      key={column}
                      className="flex min-w-0 items-center justify-between gap-3 border-b px-4 py-2.5 last:border-b-0 sm:[&:nth-last-child(-n+2)]:border-b-0 xl:[&:nth-last-child(-n+3)]:border-b-0"
                    >
                      <code className="truncate text-xs font-medium">
                        {column}
                      </code>
                      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
                        {view.column_types[index] ?? "UNKNOWN"}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="px-4 py-8 text-center text-xs text-muted-foreground">
                  No columns available
                </div>
              )}
            </div>
            <div className="flex justify-end">
              {view.managed && view.available && (
                <Button
                  onClick={save}
                  disabled={!sql.trim() || update.isPending || !dirty}
                >
                  {update.isPending ? "Saving…" : "Save changes"}
                </Button>
              )}
            </div>
          </div>
        </div>
      </CataloguePanel>
      <MaterializeViewDialog
        open={materializeOpen}
        onOpenChange={setMaterializeOpen}
        view={view}
      />
    </>
  )
}

function ViewStatus({ view }: { view: CatalogueViewRecord }) {
  const label = view.available
    ? view.fixture_path
      ? "Fixture"
      : view.managed
        ? "Managed"
        : "Unowned"
    : "Unavailable"
  const explanation = view.available
    ? view.fixture_path
      ? `Seeded from ${view.fixture_path}.`
      : view.managed
        ? "Atlas manages this DuckLake view for the user."
        : "Adopt this DuckLake view to manage it in Atlas."
    : "The referenced DuckLake view is missing."
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Badge variant={view.available ? "secondary" : "destructive"} />
        }
      >
        {label}
      </TooltipTrigger>
      <TooltipContent>{explanation}</TooltipContent>
    </Tooltip>
  )
}

function MaterializationStatus({
  status,
}: {
  status: NonNullable<CatalogueViewRecord["materialization"]>["status"]
}) {
  const label = status.replace("_", " ")
  return (
    <Badge
      variant={
        status === "deleting" || status === "blocked_schema" || status === "failed"
          ? "destructive"
          : status === "live" ||
              status === "creating" ||
              status === "backfilling"
            ? "default"
            : "secondary"
      }
    >
      {label}
    </Badge>
  )
}

function MaterializationCell({
  materialization,
}: {
  materialization: CatalogueViewRecord["materialization"]
}) {
  if (!materialization)
    return (
      <div>
        <Badge variant="outline">Virtual</Badge>
        <div className="mt-1 text-[10px] text-muted-foreground">
          Evaluated on read
        </div>
      </div>
    )
  return (
    <div>
      <MaterializationStatus status={materialization.status} />
      <div className="mt-1 text-[10px] text-muted-foreground">
        {materialization.status === "creating"
          ? "Creating empty stored table"
          : materialization.status === "backfilling"
            ? "Live CDC with historical backfill"
          : materialization.status === "live"
            ? "Live maintenance enabled"
            : "Maintenance paused"}
      </div>
    </div>
  )
}
