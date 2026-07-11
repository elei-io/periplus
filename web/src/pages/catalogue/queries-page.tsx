import { useEffect, useState } from "react"
import { ArchiveIcon, Clock3Icon, DatabaseZapIcon, FileCode2Icon, RotateCcwIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useArchiveSavedQuery, useRestoreSavedQueryRevision, useSavedQueries, useSavedQuery } from "@/hooks/use-saved-queries"
import { MaterializeQueryDialog } from "@/components/catalogue/materialize-query-dialog"

export function CatalogueQueriesPage() {
  const list = useSavedQueries()
  const queries = list.data?.items ?? []
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const activeId = selectedId ?? queries[0]?.id ?? null
  const detail = useSavedQuery(activeId)
  const [revisionId, setRevisionId] = useState<string | null>(null)
  const [materializeOpen, setMaterializeOpen] = useState(false)
  const restore = useRestoreSavedQueryRevision()
  const archive = useArchiveSavedQuery()
  const query = detail.data
  const revision = query?.revisions.find((item) => item.id === revisionId) ?? query?.revisions[0]

  useEffect(() => setRevisionId(null), [activeId])

  return (
    <div className="grid min-h-0 w-full gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
      <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card/80">
        <div className="flex items-center gap-2 border-b px-3 py-3"><FileCode2Icon className="size-4 text-primary" /><span className="font-medium">Saved queries</span><Badge variant="outline">{queries.length}</Badge></div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {queries.map((item) => (
            <button key={item.id} type="button" onClick={() => setSelectedId(item.id)} className={`mb-1 w-full rounded-lg px-3 py-2 text-left ${activeId === item.id ? "bg-primary/10 text-primary" : "hover:bg-muted"}`}>
              <span className="block truncate text-xs font-medium">{item.name}</span>
              <span className="text-[10px] text-muted-foreground">Revision {item.current_revision}</span>
            </button>
          ))}
          {!list.isLoading && queries.length === 0 && <div className="p-6 text-center text-xs text-muted-foreground">No saved queries yet. Save one from Catalogue SQL.</div>}
        </div>
      </section>
      {query && revision ? (
        <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card/80">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
            <div><div className="flex items-center gap-2"><h1 className="font-medium">{query.name}</h1><Badge variant="secondary">v{query.current_revision}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{query.description || "No description"}</p></div>
            <div className="flex gap-2"><Button size="sm" variant="outline" onClick={() => setMaterializeOpen(true)}><DatabaseZapIcon />Materialize</Button><Button nativeButton={false} size="sm" variant="outline" render={<a href={`/catalogue/sql?query=${query.id}${revision.id === query.current_revision_id ? "" : `&revision=${revision.revision}`}`} />}><FileCode2Icon />{revision.id === query.current_revision_id ? "Open in workbench" : `Open revision ${revision.revision}`}</Button><Button size="sm" variant="outline" onClick={() => { if (window.confirm(`Archive ${query.name}?`)) archive.mutate(query.id) }}><ArchiveIcon />Archive</Button></div>
          </div>
          <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_15rem]">
            <div className="min-h-0 overflow-y-auto p-4">
              <div className="mb-2 flex items-center justify-between"><div className="flex items-center gap-2"><Badge variant="outline">Revision {revision.revision}</Badge><span className="text-[10px] text-muted-foreground">{new Date(revision.created_at).toLocaleString()}</span></div>{revision.id !== query.current_revision_id && <Button size="sm" variant="outline" onClick={() => restore.mutate({ query, revisionId: revision.id })} disabled={restore.isPending}><RotateCcwIcon />Restore as new revision</Button>}</div>
              <pre className="min-h-72 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted/20 p-4 font-mono text-xs leading-relaxed">{revision.sql}</pre>
              {revision.change_note && <p className="mt-2 text-xs text-muted-foreground">{revision.change_note}</p>}
            </div>
            <aside className="min-h-0 overflow-y-auto border-t p-2 lg:border-t-0 lg:border-l">
              <div className="px-2 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">History</div>
              {query.revisions.map((item) => <button type="button" key={item.id} onClick={() => setRevisionId(item.id)} className={`mb-1 flex w-full items-center justify-between rounded-md px-2 py-2 text-left text-xs ${revision.id === item.id ? "bg-primary/10 text-primary" : "hover:bg-muted"}`}><span>Revision {item.revision}</span><Clock3Icon className="size-3" /></button>)}
            </aside>
          </div>
          <MaterializeQueryDialog
            open={materializeOpen}
            onOpenChange={setMaterializeOpen}
            source={{ kind: "query", revision, label: `${query.name} · revision ${revision.revision}` }}
          />
        </section>
      ) : <div className="flex items-center justify-center rounded-xl border bg-card/50 text-sm text-muted-foreground">Select or save a query.</div>}
    </div>
  )
}
