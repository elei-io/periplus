import { useState } from "react"

import { Button } from "@/components/ui/button"
import { formatSql } from "@/components/catalogue/sql-format"
import { SqlEditor } from "@/components/catalogue/sql-editor"
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
import { useCreateCatalogueView } from "@/hooks/use-catalogue-views"

export function SaveViewDialog({
  open,
  onOpenChange,
  sql,
  queryRevisionId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  sql: string
  queryRevisionId?: string
}) {
  const [slug, setSlug] = useState("")
  const [description, setDescription] = useState("")
  const [draftSql, setDraftSql] = useState(() => formatSql(sql))
  const create = useCreateCatalogueView()

  function reset() {
    setSlug("")
    setDescription("")
    setDraftSql(formatSql(sql))
  }

  function submit() {
    create.mutate(
      {
        slug,
        description: description || undefined,
        sql: draftSql,
        created_from_query_revision_id: queryRevisionId,
      },
      {
        onSuccess: () => {
          reset()
          onOpenChange(false)
        },
      }
    )
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) reset()
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Create view</DialogTitle>
          <DialogDescription>
            Create a persistent DuckLake view in the views schema.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="view-slug">Slug</Label>
            <Input
              id="view-slug"
              value={slug}
              onChange={(event) =>
                setSlug(
                  event.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-")
                )
              }
              placeholder="follower-mentions"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="view-description">Description</Label>
            <Textarea
              id="view-description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What this view is useful for"
            />
          </div>
          <div className="overflow-hidden rounded-xl border bg-card">
            <div className="border-b bg-muted/20 px-3 py-2 text-[10px] font-medium tracking-wide text-muted-foreground uppercase">
              View SQL
            </div>
            <SqlEditor
              value={draftSql}
              onChange={setDraftSql}
              height="240px"
              ariaLabel="SQL for the new view"
              enableCssSelect
            />
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button
            onClick={submit}
            disabled={!slug || !draftSql.trim() || create.isPending}
          >
            {create.isPending ? "Creating…" : "Create view"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
