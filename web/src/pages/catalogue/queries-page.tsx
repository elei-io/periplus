import { useEffect, useState } from "react"
import { ArchiveIcon, ArrowLeftIcon, ArrowUpRightIcon, Clock3Icon, DatabaseZapIcon, FileCode2Icon, HistoryIcon, RotateCcwIcon, SparklesIcon } from "lucide-react"

import { MaterializeQueryDialog } from "@/components/catalogue/materialize-query-dialog"
import { CatalogueEmptyState, CatalogueHero, CataloguePanel } from "@/components/catalogue/catalogue-workspace"
import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useArchiveSavedQuery, useRestoreSavedQueryRevision, useSavedQueries, useSavedQuery } from "@/hooks/use-saved-queries"
import { useCatalogueMaterialization, useRebuildCatalogueMaterialization } from "@/hooks/use-catalogue-materializations"
import { CatalogueMaterializationDetail } from "@/pages/catalogue/materialization-detail-page"
import type { CatalogueMaterializationSummary } from "@/types/catalogue"

export function CatalogueQueriesPage({ queryId }: { queryId?: string }) {
  return queryId ? <QueryDetailPage queryId={queryId} /> : <QueryListPage />
}

function QueryListPage() {
  const list = useSavedQueries()
  const queries = list.data?.items ?? []
  const revisions = queries.reduce((sum, query) => sum + query.current_revision, 0)

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <CatalogueHero icon={FileCode2Icon} eyebrow="Reusable logic" title="Saved queries" description="Browse versioned SQL definitions, then open one to inspect its history, edit it in the workbench, or promote it to durable data.">
        <Badge variant="outline" className="h-7 bg-background/40 px-3">{queries.length} queries</Badge>
        <Badge variant="outline" className="h-7 bg-background/40 px-3">{revisions} revisions</Badge>
        <Button nativeButton={false} render={<a href="/catalogue/sql" />} className="rounded-full px-4"><SparklesIcon />New in SQL</Button>
      </CatalogueHero>

      {queries.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {queries.map((query) => (
            <a key={query.id} href={`/catalogue/queries/${query.id}`} className="group relative overflow-hidden rounded-2xl border bg-card/70 p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-lg">
              <div className="pointer-events-none absolute -top-12 -right-12 size-32 rounded-full bg-primary/0 blur-2xl transition-colors group-hover:bg-primary/10" />
              <div className="relative flex items-start justify-between gap-4">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary"><FileCode2Icon className="size-4" /></span>
                <ArrowUpRightIcon className="size-4 text-muted-foreground transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5 group-hover:text-primary" />
              </div>
              <div className="relative mt-4"><h2 className="truncate text-sm font-semibold">{query.name}</h2><p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">{query.description || "Reusable catalogue SQL with immutable revision history."}</p></div>
              {query.materialization && <div className="relative mt-4 grid grid-cols-2 gap-2 rounded-xl border bg-muted/15 p-3 text-[10px]"><div><div className="text-muted-foreground">Durable rows</div><div className="mt-0.5 font-medium tabular-nums">{query.materialization.row_count.toLocaleString()}</div></div><div><div className="text-muted-foreground">Storage</div><div className="mt-0.5 font-medium tabular-nums">{formatBytes(query.materialization.storage_bytes)}</div></div></div>}
              <div className="relative mt-4 flex flex-wrap items-center justify-between gap-2 border-t pt-3"><span className="flex items-center gap-1.5 text-[10px] text-muted-foreground"><HistoryIcon className="size-3" />{query.current_revision} revision{query.current_revision === 1 ? "" : "s"}</span><div className="flex items-center gap-1.5">{query.materialization ? <MaterializationStatus status={query.materialization.status} /> : <Badge variant="outline">Virtual</Badge>}{query.materialization && !query.materialization.definition_is_current && <Badge variant="outline" className="border-amber-500/30 text-amber-500">Revision drift</Badge>}<Badge variant="secondary">v{query.current_revision}</Badge></div></div>
            </a>
          ))}
        </div>
      ) : !list.isLoading && (
        <CataloguePanel><CatalogueEmptyState icon={FileCode2Icon} title="No saved queries yet" description="Start in Catalogue SQL, then save useful analysis as a versioned query." action={<Button nativeButton={false} render={<a href="/catalogue/sql" />}>Open SQL workbench</Button>} className="min-h-[28rem]" /></CataloguePanel>
      )}
    </div>
  )
}

function QueryDetailPage({ queryId }: { queryId: string }) {
  const detail = useSavedQuery(queryId)
  const query = detail.data
  const materializationId = query?.materialization?.id
  const materializationQuery = useCatalogueMaterialization(query?.materialization?.id)
  const [revisionId, setRevisionId] = useState<string | null>(null)
  const [materializeOpen, setMaterializeOpen] = useState(false)
  const restore = useRestoreSavedQueryRevision()
  const archive = useArchiveSavedQuery()
  const rebuild = useRebuildCatalogueMaterialization()
  const [revisionDecisionDismissed, setRevisionDecisionDismissed] = useState(false)
  const revision = query?.revisions.find((item) => item.id === revisionId) ?? query?.revisions[0]

  useEffect(() => {
    if (window.location.hash !== "#durable-data" || !materializationId) return
    const frame = window.requestAnimationFrame(() => document.getElementById("durable-data")?.scrollIntoView({ behavior: "smooth", block: "start" }))
    return () => window.cancelAnimationFrame(frame)
  }, [materializationId])

  if (!query || !revision) {
    return <CataloguePanel><CatalogueEmptyState icon={FileCode2Icon} title={detail.isLoading ? "Loading query…" : "Query not found"} description={detail.isLoading ? "Fetching its revision history and SQL definition." : "This query may have been archived or removed."} action={!detail.isLoading ? <Button nativeButton={false} render={<a href="/catalogue/queries" />}>Back to queries</Button> : undefined} className="min-h-[32rem]" /></CataloguePanel>
  }

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <div><Button nativeButton={false} render={<a href="/catalogue/queries" />} variant="ghost"><ArrowLeftIcon />All saved queries</Button></div>
      <CatalogueHero icon={FileCode2Icon} eyebrow={`Saved query · revision ${revision.revision}`} title={query.name} description={query.description || "Reusable catalogue SQL with immutable revision history."}>
        {query.materialization ? <Button nativeButton={false} render={<a href="#durable-data" />} size="sm"><DatabaseZapIcon />Durable data</Button> : <Button size="sm" onClick={() => setMaterializeOpen(true)}><DatabaseZapIcon />Materialize</Button>}
        <Button nativeButton={false} size="sm" variant="outline" render={<a href={`/catalogue/sql?query=${query.id}${revision.id === query.current_revision_id ? "" : `&revision=${revision.revision}`}`} />}><FileCode2Icon />{revision.id === query.current_revision_id ? "Open in SQL" : `Open v${revision.revision}`}</Button>
        <Tooltip>
          <TooltipTrigger render={<span className="inline-flex" />}>
            <Button size="sm" variant="ghost" disabled={Boolean(query.materialization) || archive.isPending} onClick={() => { if (window.confirm(`Archive ${query.name}?`)) archive.mutate(query.id) }}><ArchiveIcon />Archive</Button>
          </TooltipTrigger>
          {query.materialization && <TooltipContent>Dematerialize this query before archiving it.</TooltipContent>}
        </Tooltip>
      </CatalogueHero>

      <CataloguePanel className="grid min-h-[34rem] lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-h-0 overflow-y-auto p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2"><Badge>Revision {revision.revision}</Badge>{revision.id === query.current_revision_id && <Badge variant="outline" className="border-emerald-500/30 text-emerald-500">Current</Badge>}<span className="text-[10px] text-muted-foreground">{new Date(revision.created_at).toLocaleString()}</span></div>{revision.id !== query.current_revision_id && <Button size="sm" variant="outline" onClick={() => restore.mutate({ query, revisionId: revision.id })} disabled={restore.isPending}><RotateCcwIcon />Restore as new revision</Button>}</div>
          <div className="overflow-hidden rounded-2xl border bg-[#0d1117] shadow-inner"><div className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-[10px] text-white/45"><span>DUCKDB SQL</span><span>read-only definition</span></div><SqlEditor value={formatSql(revision.sql)} readOnly height="420px" ariaLabel={`Read-only SQL for revision ${revision.revision}`} /></div>
          {revision.change_note && <div className="mt-3 rounded-xl border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">{revision.change_note}</div>}
        </div>
        <aside className="min-h-0 overflow-y-auto border-t bg-muted/10 p-3 lg:border-t-0 lg:border-l">
          <div className="mb-2 flex items-center gap-2 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-muted-foreground"><Clock3Icon className="size-3" />Revision history</div>
          {query.revisions.map((item) => <button type="button" key={item.id} onClick={() => setRevisionId(item.id)} className={`mb-1.5 w-full rounded-xl border p-3 text-left transition-colors ${revision.id === item.id ? "border-primary/25 bg-primary/10" : "border-transparent hover:bg-muted/40"}`}><div className="flex items-center justify-between"><span className="text-xs font-medium">Revision {item.revision}</span>{item.id === query.current_revision_id && <span className="size-1.5 rounded-full bg-emerald-500" />}</div><div className="mt-1 text-[10px] text-muted-foreground">{new Date(item.created_at).toLocaleDateString()}</div></button>)}
        </aside>
      </CataloguePanel>
      <section id="durable-data" className="scroll-mt-20">
        {query.materialization ? (
          materializationQuery.data ? <div className="grid gap-3">{!query.materialization.definition_is_current && !revisionDecisionDismissed && <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-700 dark:text-amber-300"><div><div className="font-medium">Durable data uses revision {query.materialization.active_query_revision}</div><p className="mt-1">Revision {query.current_revision} is current. Saving it did not pause or mutate the existing materialization.</p></div><div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" onClick={() => setRevisionDecisionDismissed(true)}>Keep revision {query.materialization.active_query_revision}</Button><Button size="sm" onClick={() => rebuild.mutate({ materialization: materializationQuery.data, targetQueryRevisionId: query.current_revision_id })} disabled={rebuild.isPending}>{rebuild.isPending ? "Starting rebuild…" : `Rebuild with revision ${query.current_revision}`}</Button></div></div>}<CatalogueMaterializationDetail materialization={materializationQuery.data} /></div> : <CataloguePanel><CatalogueEmptyState icon={DatabaseZapIcon} title="Loading durable data…" description="Fetching maintenance, coverage, schema, and storage state." className="min-h-64" /></CataloguePanel>
        ) : (
          <CataloguePanel>
            <CatalogueEmptyState icon={DatabaseZapIcon} title="Durable data" description="This query is virtual. Materialize it to maintain one durable DuckLake table without rescanning all retained evidence on every read." action={<Button onClick={() => setMaterializeOpen(true)}><DatabaseZapIcon />Materialize query</Button>} className="min-h-64" />
          </CataloguePanel>
        )}
      </section>
      <MaterializeQueryDialog open={materializeOpen} onOpenChange={setMaterializeOpen} source={{ kind: "query", revision, label: `${query.name} · revision ${revision.revision}` }} />
    </div>
  )
}

function MaterializationStatus({ status }: { status: CatalogueMaterializationSummary["status"] }) {
  const label = status === "full_refresh" ? "Full refresh" : status === "backfilling" ? "Backfilling" : status === "degraded" ? "Needs attention" : status === "source_changing" ? "Changing source" : status === "source_changed" ? "Source changed" : status[0].toUpperCase() + status.slice(1)
  return <Badge variant={status === "degraded" || status === "dematerializing" || status === "source_changing" || status === "source_changed" ? "destructive" : status === "live" || status === "backfilling" ? "default" : "secondary"}>{label}</Badge>
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`
  return `${(value / 1024 ** 3).toFixed(1)} GB`
}
