import { useEffect, useState } from "react"
import { ArrowLeftIcon, ArrowRightIcon, BracesIcon, Columns3Icon, DatabaseIcon, DatabaseZapIcon, RefreshCwIcon, ShieldCheckIcon, SparklesIcon, Trash2Icon, TriangleAlertIcon, UnlinkIcon } from "lucide-react"

import { MaterializeQueryDialog } from "@/components/catalogue/materialize-query-dialog"
import { CatalogueEmptyState, CatalogueHero, CataloguePanel } from "@/components/catalogue/catalogue-workspace"
import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useAdoptCatalogueView, useCatalogueViews, useDetachCatalogueView, useDropCatalogueView, useUpdateCatalogueView } from "@/hooks/use-catalogue-views"
import { useCatalogueMaterialization } from "@/hooks/use-catalogue-materializations"
import { CatalogueMaterializationDetail } from "@/pages/catalogue/materialization-detail-page"
import type { CatalogueViewRecord } from "@/types/catalogue"

export function CatalogueViewsPage({ viewId }: { viewId?: string }) {
  const viewsQuery = useCatalogueViews()
  const views = viewsQuery.data?.items ?? []
  const managed = views.filter((view) => view.managed).length
  const selected = viewId ? views.find((view) => view.id === viewId || view.ducklake_view_uuid === viewId) : null

  if (viewId) {
    if (!selected) {
      return <CataloguePanel><CatalogueEmptyState icon={BracesIcon} title={viewsQuery.isLoading ? "Loading view…" : "View not found"} description={viewsQuery.isLoading ? "Fetching its DuckLake definition and Atlas metadata." : "The view may have been replaced or removed."} action={!viewsQuery.isLoading ? <Button nativeButton={false} render={<a href="/catalogue/views" />}>Back to views</Button> : undefined} className="min-h-[32rem]" /></CataloguePanel>
    }
    return <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto"><div><Button nativeButton={false} render={<a href="/catalogue/views" />} variant="ghost"><ArrowLeftIcon />All views</Button></div><ViewDetail key={selected.ducklake_view_uuid} view={selected} /></div>
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <CatalogueHero icon={BracesIcon} eyebrow="Live catalogue layer" title="Views" description="Browse named DuckLake relations, then open one to inspect its schema, edit its SQL, or promote it to durable data.">
        <Badge variant="outline" className="h-7 bg-background/40 px-3">{views.length} views</Badge>
        <Badge variant="outline" className="h-7 bg-background/40 px-3">{managed} managed</Badge>
        <Button nativeButton={false} render={<a href="/catalogue/sql" />} className="rounded-full px-4"><SparklesIcon />Create in SQL</Button>
      </CatalogueHero>
      <div className="flex justify-end"><Tooltip><TooltipTrigger render={<Button variant="outline" size="sm" aria-label="Refresh catalogue views" onClick={() => void viewsQuery.refetch()} disabled={viewsQuery.isFetching} />}><RefreshCwIcon className={viewsQuery.isFetching ? "animate-spin" : ""} />Refresh catalogue</TooltipTrigger><TooltipContent>Refresh DuckLake views and Atlas references</TooltipContent></Tooltip></div>
      {views.length > 0 ? (
        <CataloguePanel>
          <Table>
            <TableHeader className="bg-muted/20">
              <TableRow>
                <TableHead className="pl-4">View</TableHead>
                <TableHead>Ownership</TableHead>
                <TableHead className="text-right">Columns</TableHead>
                <TableHead>Evaluation</TableHead>
                <TableHead>State</TableHead>
                <TableHead className="text-right">Rows</TableHead>
                <TableHead className="text-right">Storage</TableHead>
                <TableHead>Availability</TableHead>
                <TableHead>Updated</TableHead>
                <TableHead className="w-20" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {views.map((view) => {
                const href = `/catalogue/views/${view.id ?? view.ducklake_view_uuid}`
                return (
                  <TableRow key={view.ducklake_view_uuid} className={view.provisioned_by === "system" ? "group bg-primary/[0.035]" : "group"}>
                    <TableCell className="max-w-sm py-3 pl-4">
                      <a href={href} className="flex min-w-0 items-center gap-3 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/30">
                        <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">{view.provisioned_by === "system" ? <ShieldCheckIcon className="size-3.5" /> : <BracesIcon className="size-3.5" />}</span>
                        <span className="min-w-0"><span className="block truncate font-medium group-hover:text-primary">{view.display_name}</span><span className="block truncate font-mono text-[10px] text-muted-foreground">{view.qualified_name}</span></span>
                      </a>
                    </TableCell>
                    <TableCell><Badge variant={view.provisioned_by === "system" ? "default" : view.managed ? "secondary" : "outline"}>{view.provisioned_by === "system" ? "System" : view.managed ? "User" : "Unowned"}</Badge></TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{view.columns.length}</TableCell>
                    <TableCell><Badge variant={view.materialization ? "secondary" : "outline"}>{view.materialization ? "Materialized" : "On read"}</Badge></TableCell>
                    <TableCell>{view.materialization ? <MaterializationStatus status={view.materialization.status} /> : <span className="text-muted-foreground">Virtual</span>}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{view.materialization?.row_count.toLocaleString() ?? "—"}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{view.materialization ? formatBytes(view.materialization.storage_bytes) : "—"}</TableCell>
                    <TableCell><Badge variant={view.available ? "outline" : "destructive"} className={view.available ? "border-emerald-500/25 text-emerald-500" : undefined}>{view.available ? "Available" : "Missing"}</Badge></TableCell>
                    <TableCell className="text-muted-foreground">{view.updated_at ? new Date(view.updated_at).toLocaleDateString() : "—"}</TableCell>
                    <TableCell className="pr-4 text-right"><Button nativeButton={false} render={<a href={href} />} size="sm" variant="ghost">Open<ArrowRightIcon /></Button></TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CataloguePanel>
      ) : !viewsQuery.isLoading && <CataloguePanel><CatalogueEmptyState icon={BracesIcon} title="No views yet" description="Turn useful SQL into a lightweight, reusable DuckLake view." action={<Button nativeButton={false} render={<a href="/catalogue/sql" />}>Open SQL workbench</Button>} className="min-h-[28rem]" /></CataloguePanel>}
    </div>
  )
}

function ViewDetail({ view }: { view: CatalogueViewRecord }) {
  const [sql, setSql] = useState(() => formatSql(view.sql))
  const [displayName, setDisplayName] = useState(view.display_name)
  const [description, setDescription] = useState(view.description ?? "")
  const update = useUpdateCatalogueView()
  const adopt = useAdoptCatalogueView()
  const detach = useDetachCatalogueView()
  const drop = useDropCatalogueView()
  const [materializeOpen, setMaterializeOpen] = useState(false)
  const materialization = view.materialization
  const materializationQuery = useCatalogueMaterialization(materialization?.id)
  const dirty = sql !== formatSql(view.sql) || displayName !== view.display_name || description !== (view.description ?? "")

  useEffect(() => {
    if (window.location.hash !== "#durable-data" || !materialization?.id) return
    const frame = window.requestAnimationFrame(() => document.getElementById("durable-data")?.scrollIntoView({ behavior: "smooth", block: "start" }))
    return () => window.cancelAnimationFrame(frame)
  }, [materialization?.id])

  const save = () => {
    const definitionChanged = sql.trim() !== view.sql.trim()
    if (definitionChanged && materialization && !window.confirm("Change the source definition used by its materialization? Existing materialized rows will not change automatically; refresh or rebuild them explicitly.")) return
    update.mutate({ view, sql, display_name: displayName, description })
  }

  return (
    <>
      <CataloguePanel className="flex min-h-0 flex-col">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b bg-gradient-to-r from-primary/5 to-transparent px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary"><DatabaseIcon className="size-4" /></span>
            <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="truncate text-base font-semibold">{view.display_name}</h2><ViewStatus view={view} />{dirty && <Badge variant="outline" className="border-amber-500/30 text-amber-500">Unsaved</Badge>}</div><div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">{view.qualified_name} · {view.ducklake_view_uuid}</div></div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {view.available && view.managed && (materialization ? <Button nativeButton={false} render={<a href="#durable-data" />} size="sm"><DatabaseZapIcon />Durable data</Button> : <Button size="sm" onClick={() => setMaterializeOpen(true)}><DatabaseZapIcon />Materialize</Button>)}
            {!view.managed && <Button size="sm" variant="outline" onClick={() => adopt.mutate(view)} disabled={adopt.isPending}>Adopt view</Button>}
            {view.managed && <Tooltip><TooltipTrigger render={<span className="inline-flex" />}><Button size="sm" variant="outline" onClick={() => detach.mutate(view)} disabled={Boolean(materialization) || detach.isPending}><UnlinkIcon />Detach</Button></TooltipTrigger><TooltipContent>{materialization ? "Dematerialize this view before detaching its Atlas reference." : "Remove only the Atlas reference; keep the DuckLake view."}</TooltipContent></Tooltip>}
            {view.managed && <Tooltip><TooltipTrigger render={<span className="inline-flex" />}><Button size="sm" variant="destructive" onClick={() => { if (window.confirm(`Permanently drop ${view.qualified_name} from DuckLake? Its Atlas reference will also be archived.`)) drop.mutate(view) }} disabled={Boolean(materialization) || drop.isPending}><Trash2Icon />Drop</Button></TooltipTrigger><TooltipContent>{materialization ? "Dematerialize this view before deleting its DuckLake object." : "Delete the DuckLake object and archive its Atlas reference."}</TooltipContent></Tooltip>}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <div className="grid gap-4">
            {materialization && (
              <div className="flex gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">
                <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" /><div><div className="font-medium">This view feeds durable data</div><p className="mt-1 text-foreground/70">Changing its SQL replaces the DuckLake identity. Refresh or rebuild the attached materialization deliberately.</p><div className="mt-2 flex flex-wrap gap-1"><MaterializationStatus status={materialization.status} />{!materialization.definition_is_current && <Badge variant="outline" className="border-amber-500/30 text-amber-500">Definition changed</Badge>}</div></div>
              </div>
            )}

            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
              <div className="grid gap-1.5"><Label>Display name</Label><Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} disabled={!view.managed} /></div>
              <div className="grid gap-1.5"><Label>Description</Label><Input value={description} onChange={(event) => setDescription(event.target.value)} disabled={!view.managed} placeholder="What is this view useful for?" /></div>
            </div>

            <div className="overflow-hidden rounded-2xl border bg-[#0d1117] shadow-inner">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-2 text-[10px] text-white/45"><span>VIEW DEFINITION</span><div className="flex items-center gap-2"><span>{view.columns.length} output columns</span>{view.managed && view.available && <Button size="xs" variant="ghost" className="text-white/60 hover:bg-white/10 hover:text-white" onClick={() => setSql(formatSql(sql))}><SparklesIcon />Format SQL</Button>}</div></div>
              <SqlEditor value={sql} onChange={setSql} readOnly={!view.managed || !view.available} height="320px" ariaLabel={`SQL definition for ${view.qualified_name}`} />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 flex-wrap items-center gap-1.5"><Columns3Icon className="mr-1 size-3.5 text-muted-foreground" />{view.columns.map((column) => <Badge key={column} variant="outline" className="font-mono">{column}</Badge>)}{view.columns.length === 0 && <span className="text-xs text-muted-foreground">No columns available</span>}</div>
              {view.managed && view.available && <Button onClick={save} disabled={!sql.trim() || update.isPending || !dirty}>{update.isPending ? "Saving…" : "Save changes"}</Button>}
            </div>
          </div>
        </div>
      </CataloguePanel>
      <section id="durable-data" className="scroll-mt-20">
        {materialization ? (
          materializationQuery.data ? <CatalogueMaterializationDetail materialization={materializationQuery.data} /> : <CataloguePanel><CatalogueEmptyState icon={DatabaseZapIcon} title="Loading durable data…" description="Fetching maintenance, coverage, schema, and storage state." className="min-h-64" /></CataloguePanel>
        ) : (
          <CataloguePanel>
            <CatalogueEmptyState icon={DatabaseZapIcon} title="Durable data" description="This view is evaluated on every read. Materialize it to maintain one durable DuckLake table for repeated access." action={view.available && view.managed ? <Button onClick={() => setMaterializeOpen(true)}><DatabaseZapIcon />Materialize view</Button> : undefined} className="min-h-64" />
          </CataloguePanel>
        )}
      </section>
      <MaterializeQueryDialog open={materializeOpen} onOpenChange={setMaterializeOpen} source={{ kind: "view", view }} />
    </>
  )
}

function ViewStatus({ view }: { view: CatalogueViewRecord }) {
  const label = view.available ? view.provisioned_by === "system" ? "System" : view.managed ? "User" : "Unowned" : "Unavailable"
  const explanation = view.available ? view.provisioned_by === "system" ? "Atlas provides this view as a reusable system primitive." : view.managed ? "A user manages this DuckLake view in Atlas." : "Adopt this DuckLake view to manage it in Atlas." : "The referenced DuckLake view is missing."
  return <Tooltip><TooltipTrigger render={<Badge variant={view.available ? "secondary" : "destructive"} />}>{label}</TooltipTrigger><TooltipContent>{explanation}</TooltipContent></Tooltip>
}

function MaterializationStatus({ status }: { status: NonNullable<CatalogueViewRecord["materialization"]>["status"] }) {
  const label = status === "full_refresh" ? "Full refresh" : status === "backfilling" ? "Backfilling" : status === "degraded" ? "Needs attention" : status === "source_changing" ? "Changing source" : status === "source_changed" ? "Source changed" : status[0].toUpperCase() + status.slice(1)
  return <Badge variant={status === "degraded" || status === "dematerializing" || status === "source_changing" || status === "source_changed" ? "destructive" : status === "live" || status === "backfilling" ? "default" : "secondary"}>{label}</Badge>
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
