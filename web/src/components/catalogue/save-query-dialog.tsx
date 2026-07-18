import { useState } from "react"

import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateSavedQuery,
  useUpdateSavedQuery,
} from "@/hooks/use-saved-queries"
import type { SavedQueryDetail } from "@/types/catalogue"

export function SaveQueryDialog({
  open,
  onOpenChange,
  sql,
  query,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  sql: string
  query: SavedQueryDetail | null
  onSaved: (query: SavedQueryDetail) => void
}) {
  const [slug, setSlug] = useState(query?.slug ?? "")
  const [description, setDescription] = useState(query?.description ?? "")
  const [note, setNote] = useState("")
  const [draftSql, setDraftSql] = useState(() => formatSql(sql))
  const create = useCreateSavedQuery()
  const update = useUpdateSavedQuery()

  function reset() {
    setSlug(query?.slug ?? "")
    setDescription(query?.description ?? "")
    setNote("")
    setDraftSql(formatSql(sql))
  }

  function submit() {
    const options = {
      onSuccess: (saved: SavedQueryDetail) => {
        onSaved(saved)
        reset()
        onOpenChange(false)
      },
    }
    if (query)
      update.mutate(
        { query, sql: draftSql, slug, description, change_note: note },
        options
      )
    else
      create.mutate(
        {
          slug,
          description: description || undefined,
          sql: draftSql,
          change_note: note || undefined,
        },
        options
      )
  }
  const pending = create.isPending || update.isPending
  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) reset()
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            {query
              ? `Save revision ${query.current_revision + 1}`
              : "Create query"}
          </DialogTitle>
          <DialogDescription>
            Saved queries keep immutable SQL revision history in Atlas.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label>Slug</Label>
            <Input
              value={slug}
              onChange={(event) =>
                setSlug(
                  event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-")
                )
              }
              placeholder="latest-successful-crawls"
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Description</Label>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Change note</Label>
            <Input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={query ? "What changed?" : "Initial version"}
            />
          </div>
          <div className="overflow-hidden rounded-xl border bg-card">
            <div className="border-b bg-muted/20 px-3 py-2 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              DuckDB SQL
            </div>
            <SqlEditor
              value={draftSql}
              onChange={setDraftSql}
              height="260px"
              ariaLabel={
                query
                  ? "SQL for the new query revision"
                  : "SQL for the new saved query"
              }
            />
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button
            onClick={submit}
            disabled={!slug.trim() || !draftSql.trim() || pending}
          >
            {pending ? "Saving…" : query ? "Save revision" : "Create query"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
