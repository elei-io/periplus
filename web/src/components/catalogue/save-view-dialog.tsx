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

export function SaveViewDialog({ open, onOpenChange, sql, queryRevisionId }: { open: boolean; onOpenChange: (open: boolean) => void; sql: string; queryRevisionId?: string }) {
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const create = useCreateCatalogueView()

  function reset() {
    setName("")
    setDisplayName("")
    setDescription("")
  }

  function submit() {
    create.mutate(
      { name, display_name: displayName || undefined, description: description || undefined, sql, created_from_query_revision_id: queryRevisionId },
      { onSuccess: () => { reset(); onOpenChange(false) } }
    )
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) reset(); onOpenChange(nextOpen) }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save as view</DialogTitle>
          <DialogDescription>Create a persistent DuckLake view in the views schema.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="view-name">View name</Label>
            <Input id="view-name" value={name} onChange={(event) => setName(event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} placeholder="follower_mentions" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="view-display-name">Display name</Label>
            <Input id="view-display-name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Follower mentions" />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="view-description">Description</Label>
            <Textarea id="view-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this view is useful for" />
          </div>
          <div className="overflow-hidden rounded-xl border bg-card">
            <div className="border-b bg-muted/20 px-3 py-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">View SQL · read only</div>
            <SqlEditor value={formatSql(sql)} readOnly height="150px" ariaLabel="Read-only SQL for the new view" />
          </div>
        </div>
        <DialogFooter showCloseButton>
          <Button onClick={submit} disabled={!name || create.isPending}>{create.isPending ? "Creating…" : "Create view"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
