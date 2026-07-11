import { useEffect, useState } from "react"
import { BracesIcon, DatabaseIcon, RefreshCwIcon, Trash2Icon, UnlinkIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
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
import type { CatalogueViewRecord } from "@/types/catalogue"

export function CatalogueViewsPage() {
  const viewsQuery = useCatalogueViews()
  const views = viewsQuery.data?.items ?? []
  const [selectedUuid, setSelectedUuid] = useState<string | null>(null)
  const selected = views.find((view) => view.ducklake_view_uuid === selectedUuid) ?? views[0] ?? null

  useEffect(() => {
    if (selected && selected.ducklake_view_uuid !== selectedUuid) setSelectedUuid(selected.ducklake_view_uuid)
  }, [selected, selectedUuid])

  return (
    <div className="grid min-h-0 w-full gap-4 lg:grid-cols-[19rem_minmax(0,1fr)]">
      <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card/80">
        <div className="flex items-center justify-between border-b px-3 py-2.5">
          <div className="flex items-center gap-2">
            <BracesIcon className="size-4 text-primary" />
            <span className="font-medium">Views</span>
            <Badge variant="outline">{views.length}</Badge>
          </div>
          <Tooltip>
            <TooltipTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Refresh catalogue views"
                  onClick={() => void viewsQuery.refetch()}
                  disabled={viewsQuery.isFetching}
                />
              }
            >
              <RefreshCwIcon
                className={viewsQuery.isFetching ? "animate-spin" : ""}
              />
            </TooltipTrigger>
            <TooltipContent>Refresh DuckLake views and Atlas references</TooltipContent>
          </Tooltip>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {views.map((view) => (
            <button
              type="button"
              key={view.ducklake_view_uuid}
              onClick={() => setSelectedUuid(view.ducklake_view_uuid)}
              className={`mb-1 w-full rounded-lg px-3 py-2 text-left transition-colors ${selected?.ducklake_view_uuid === view.ducklake_view_uuid ? "bg-primary/10 text-primary" : "hover:bg-muted"}`}
            >
              <span className="block truncate text-xs font-medium">{view.display_name}</span>
              <span className="block truncate font-mono text-[10px] text-muted-foreground">{view.qualified_name}</span>
            </button>
          ))}
          {!viewsQuery.isLoading && views.length === 0 && <div className="p-6 text-center text-xs text-muted-foreground">No views yet. Save one from Catalogue SQL.</div>}
        </div>
      </section>
      {selected ? <ViewDetail key={selected.ducklake_view_uuid} view={selected} /> : <div className="flex items-center justify-center rounded-xl border bg-card/50 text-sm text-muted-foreground">Select or create a view.</div>}
    </div>
  )
}

function ViewDetail({ view }: { view: CatalogueViewRecord }) {
  const [sql, setSql] = useState(view.sql)
  const [displayName, setDisplayName] = useState(view.display_name)
  const [description, setDescription] = useState(view.description ?? "")
  const update = useUpdateCatalogueView()
  const adopt = useAdoptCatalogueView()
  const detach = useDetachCatalogueView()
  const drop = useDropCatalogueView()

  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border bg-card/80">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <div className="flex items-center gap-2">
            <DatabaseIcon className="size-4 text-primary" />
            <h1 className="font-medium">{view.display_name}</h1>
            <ViewStatus view={view} />
          </div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">{view.qualified_name} · {view.ducklake_view_uuid}</div>
        </div>
        <div className="flex items-center gap-2">
          {!view.managed && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => adopt.mutate(view)}
                    disabled={adopt.isPending}
                  />
                }
              >
                Adopt
              </TooltipTrigger>
              <TooltipContent>
                Add an Atlas reference without changing the DuckLake view
              </TooltipContent>
            </Tooltip>
          )}
          {view.managed && (
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => detach.mutate(view)}
                    disabled={detach.isPending}
                  />
                }
              >
                <UnlinkIcon />Detach
              </TooltipTrigger>
              <TooltipContent>
                Remove only the Atlas reference. The DuckLake view remains and becomes unowned.
              </TooltipContent>
            </Tooltip>
          )}
          {view.managed && (
            <Tooltip>
              <TooltipTrigger
                render={
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
                    disabled={drop.isPending}
                  />
                }
              >
                <Trash2Icon />Drop view
              </TooltipTrigger>
              <TooltipContent>
                Delete the DuckLake view itself and archive its Atlas reference
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="grid gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5"><Label>Display name</Label><Input value={displayName} onChange={(event) => setDisplayName(event.target.value)} disabled={!view.managed} /></div>
            <div className="grid gap-1.5"><Label>Description</Label><Input value={description} onChange={(event) => setDescription(event.target.value)} disabled={!view.managed} /></div>
          </div>
          <div className="grid gap-1.5">
            <Label>View SQL</Label>
            <Textarea className="min-h-72 resize-y font-mono text-xs" value={sql} onChange={(event) => setSql(event.target.value)} disabled={!view.managed || !view.available} />
          </div>
          {view.managed && view.available && (
            <div className="flex justify-end"><Button onClick={() => update.mutate({ view, sql, display_name: displayName, description })} disabled={!sql.trim() || update.isPending}>{update.isPending ? "Saving…" : "Save changes"}</Button></div>
          )}
        </div>
      </div>
    </section>
  )
}

function ViewStatus({ view }: { view: CatalogueViewRecord }) {
  const label = view.available
    ? view.managed
      ? "Managed"
      : "Unowned"
    : "Unavailable"
  const explanation = view.available
    ? view.managed
      ? "Atlas has a reference to this DuckLake view and can edit it."
      : "The view exists in DuckLake but has no Atlas reference. Adopt it to manage it here."
    : "Atlas has a reference, but the DuckLake view was changed or removed outside Atlas."
  return (
    <Tooltip>
      <TooltipTrigger render={<Badge variant={view.available ? "secondary" : "destructive"} />}>
        {label}
      </TooltipTrigger>
      <TooltipContent>{explanation}</TooltipContent>
    </Tooltip>
  )
}
