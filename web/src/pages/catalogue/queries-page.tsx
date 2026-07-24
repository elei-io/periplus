import { useState } from "react"
import {
  ArchiveIcon,
  ArrowLeftIcon,
  ArrowUpRightIcon,
  Clock3Icon,
  FileCode2Icon,
  HistoryIcon,
  RotateCcwIcon,
  SparklesIcon,
} from "lucide-react"

import {
  CatalogueEmptyState,
  CatalogueHero,
  CataloguePanel,
} from "@/components/catalogue/catalogue-workspace"
import { SaveQueryDialog } from "@/components/catalogue/save-query-dialog"
import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  useArchiveSavedQuery,
  useRestoreSavedQueryRevision,
  useSavedQueries,
  useSavedQuery,
} from "@/hooks/use-saved-queries"

export function CatalogueQueriesPage({ queryId }: { queryId?: string }) {
  return queryId ? <QueryDetailPage queryId={queryId} /> : <QueryListPage />
}

function QueryListPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const list = useSavedQueries()
  const queries = list.data?.items ?? []
  const revisions = queries.reduce(
    (sum, query) => sum + query.current_revision,
    0
  )

  return (
    <div className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto">
      <CatalogueHero
        icon={FileCode2Icon}
        eyebrow="Reusable logic"
        title="Saved queries"
        description="Browse versioned SQL definitions, inspect their history, and save reusable results as catalogue views."
      >
        <Badge variant="outline" className="h-7 bg-background/40 px-3">
          {queries.length} queries
        </Badge>
        <Badge variant="outline" className="h-7 bg-background/40 px-3">
          {revisions} revisions
        </Badge>
        <Button
          onClick={() => setCreateOpen(true)}
          className="rounded-full px-4"
        >
          <SparklesIcon />
          New query
        </Button>
      </CatalogueHero>

      {queries.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {queries.map((query) => (
            <a
              key={query.id}
              href={`/catalogue/queries/${query.id}`}
              className="group relative overflow-hidden rounded-2xl border bg-card/70 p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-lg"
            >
              <div className="pointer-events-none absolute -top-12 -right-12 size-32 rounded-full bg-primary/0 blur-2xl transition-colors group-hover:bg-primary/10" />
              <div className="relative flex items-start justify-between gap-4">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
                  <FileCode2Icon className="size-4" />
                </span>
                <ArrowUpRightIcon className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-primary" />
              </div>
              <div className="relative mt-4">
                <h2 className="truncate text-sm font-semibold">{query.slug}</h2>
                <p className="mt-1 line-clamp-2 min-h-10 text-xs leading-5 text-muted-foreground">
                  {query.description ||
                    "Reusable catalogue SQL with immutable revision history."}
                </p>
              </div>
              <div className="relative mt-4 flex flex-wrap items-center justify-between gap-2 border-t pt-3">
                <span className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                  <HistoryIcon className="size-3" />
                  {query.current_revision} revision
                  {query.current_revision === 1 ? "" : "s"}
                </span>
                <div className="flex gap-1.5">
                  {query.fixture_path && <Badge>Fixture</Badge>}
                  <Badge variant="secondary">v{query.current_revision}</Badge>
                </div>
              </div>
            </a>
          ))}
        </div>
      ) : (
        !list.isLoading && (
          <CataloguePanel>
            <CatalogueEmptyState
              icon={FileCode2Icon}
              title="No saved queries yet"
              description="Create reusable SQL with immutable revision history."
              action={
                <Button onClick={() => setCreateOpen(true)}>
                  Create query
                </Button>
              }
              className="min-h-[28rem]"
            />
          </CataloguePanel>
        )
      )}
      <SaveQueryDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        sql={"SELECT *\nFROM documents\nLIMIT 100;"}
        query={null}
        onSaved={(saved) => {
          window.history.pushState(null, "", `/catalogue/queries/${saved.id}`)
          window.dispatchEvent(new PopStateEvent("popstate"))
        }}
      />
    </div>
  )
}

function QueryDetailPage({ queryId }: { queryId: string }) {
  const detail = useSavedQuery(queryId)
  const query = detail.data
  const [revisionId, setRevisionId] = useState<string | null>(null)
  const [editOpen, setEditOpen] = useState(false)
  const restore = useRestoreSavedQueryRevision()
  const archive = useArchiveSavedQuery()
  const revision =
    query?.revisions.find((item) => item.id === revisionId) ??
    query?.revisions[0]

  if (!query || !revision) {
    return (
      <CataloguePanel>
        <CatalogueEmptyState
          icon={FileCode2Icon}
          title={detail.isLoading ? "Loading query…" : "Query not found"}
          description={
            detail.isLoading
              ? "Fetching its revision history and SQL definition."
              : "This query may have been archived or removed."
          }
          action={
            !detail.isLoading ? (
              <Button
                nativeButton={false}
                render={<a href="/catalogue/queries" />}
              >
                Back to queries
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
          render={<a href="/catalogue/queries" />}
          variant="ghost"
        >
          <ArrowLeftIcon />
          All saved queries
        </Button>
      </div>
      <CatalogueHero
        icon={FileCode2Icon}
        eyebrow={`Saved query · revision ${revision.revision}`}
        title={query.slug}
        description={
          query.description ||
          "Reusable catalogue SQL with immutable revision history."
        }
      >
        {query.fixture_path && <Badge>Fixture · {query.fixture_path}</Badge>}
        <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
          <FileCode2Icon />
          Create revision from v{revision.revision}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={archive.isPending}
          onClick={() => {
            if (window.confirm(`Archive ${query.slug}?`))
              archive.mutate(query.id)
          }}
        >
          <ArchiveIcon />
          Archive
        </Button>
      </CatalogueHero>

      <CataloguePanel className="p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant={
              revision.compiler_outcome === "unsupported"
                ? "outline"
                : "secondary"
            }
          >
            Compiler · {revision.compiler_outcome.replace("_", " ")}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {revision.compiler_version}
            {revision.catalogue_definition_revision
              ? ` · catalogue ${revision.catalogue_definition_revision}`
              : ""}
          </span>
        </div>
        {revision.compiler_diagnostics.length > 0 && (
          <div className="mt-3 space-y-2">
            {revision.compiler_diagnostics.map((diagnostic, index) => (
              <div
                key={`${diagnostic.code}-${index}`}
                className="rounded-lg border bg-muted/30 px-3 py-2 text-xs"
              >
                <span className="font-medium">{diagnostic.code}</span>
                <span className="ml-2 text-muted-foreground">
                  {diagnostic.message}
                </span>
              </div>
            ))}
          </div>
        )}
        {revision.compiler_dependencies.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {revision.compiler_dependencies.map((dependency) => (
              <Badge
                key={`${dependency.kind}:${dependency.qualified_name}`}
                variant="outline"
              >
                {dependency.qualified_name}
              </Badge>
            ))}
          </div>
        )}
      </CataloguePanel>

      <CataloguePanel className="grid min-h-[34rem] lg:grid-cols-[minmax(0,1fr)_17rem]">
        <div className="min-h-0 overflow-y-auto p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Badge>Revision {revision.revision}</Badge>
              {revision.id === query.current_revision_id && (
                <Badge
                  variant="outline"
                  className="border-emerald-500/30 text-emerald-500"
                >
                  Current
                </Badge>
              )}
              <span className="text-[10px] text-muted-foreground">
                {new Date(revision.created_at).toLocaleString()}
              </span>
            </div>
            {revision.id !== query.current_revision_id && (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  restore.mutate({ query, revisionId: revision.id })
                }
                disabled={restore.isPending}
              >
                <RotateCcwIcon />
                Restore as new revision
              </Button>
            )}
          </div>
          <div className="overflow-hidden rounded-2xl border bg-[#0d1117] shadow-inner">
            <div className="flex items-center justify-between border-b border-white/10 px-4 py-2 text-[10px] text-white/45">
              <span>DUCKDB SQL</span>
              <span>read-only definition</span>
            </div>
            <SqlEditor
              value={formatSql(revision.sql)}
              readOnly
              height="420px"
              ariaLabel={`Read-only SQL for revision ${revision.revision}`}
            />
          </div>
          {revision.change_note && (
            <div className="mt-3 rounded-xl border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
              {revision.change_note}
            </div>
          )}
        </div>
        <aside className="min-h-0 overflow-y-auto border-t bg-muted/10 p-3 lg:border-t-0 lg:border-l">
          <div className="mb-2 flex items-center gap-2 px-2 py-1 text-[10px] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
            <Clock3Icon className="size-3" />
            Revision history
          </div>
          {query.revisions.map((item) => (
            <button
              type="button"
              key={item.id}
              onClick={() => setRevisionId(item.id)}
              className={`mb-1.5 w-full rounded-xl border p-3 text-left transition-colors ${revision.id === item.id ? "border-primary/25 bg-primary/10" : "border-transparent hover:bg-muted/40"}`}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium">
                  Revision {item.revision}
                </span>
                {item.id === query.current_revision_id && (
                  <span className="size-1.5 rounded-full bg-emerald-500" />
                )}
              </div>
              <div className="mt-1 text-[10px] text-muted-foreground">
                {new Date(item.created_at).toLocaleDateString()}
              </div>
            </button>
          ))}
        </aside>
      </CataloguePanel>
      <SaveQueryDialog
        key={`${revision.id}-${editOpen}`}
        open={editOpen}
        onOpenChange={setEditOpen}
        sql={revision.sql}
        query={query}
        onSaved={() => {
          setRevisionId(null)
          setEditOpen(false)
        }}
      />
    </div>
  )
}
